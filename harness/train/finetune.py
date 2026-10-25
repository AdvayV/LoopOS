"""LoRA fine-tune a small chat model on LoopOS traces. Runs on CPU; a GPU is used if present.

    python train/finetune.py --data loopos-dataset.jsonl --val loopos-dataset.val.jsonl \\
        --base Qwen/Qwen2.5-Coder-1.5B-Instruct --out train/out --merge

Each example is {"messages": [system, user, assistant]} from the internal dataset exporter. Only the
assistant turn (the action JSON) is trained on; the prompt tokens are masked out of the loss.
Afterwards `train/to_ollama.sh train/out/merged` turns the merged model into an Ollama model.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import TrainingArguments

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def read_examples(path: Path, limit: int | None = None) -> list[list[dict[str, str]]]:
    examples = []
    with open(path) as lines:
        for number, line in enumerate(lines, 1):
            if line.strip():
                try:
                    messages = json.loads(line)["messages"]
                    if (not isinstance(messages, list) or len(messages) < 2
                            or not all(isinstance(m, dict) and isinstance(m.get("content"), str)
                                       and m.get("role") in {"system", "user", "assistant"} for m in messages)
                            or messages[-1]["role"] != "assistant" or not messages[-1]["content"].strip()):
                        raise ValueError("messages must end with a nonempty assistant reply")
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"{path}:{number}: invalid training example: {exc}") from exc
                examples.append(messages)
            if limit and len(examples) >= limit:
                break
    return examples


def encode(tokenizer, messages: list[dict[str, str]], max_length: int) -> dict[str, list[int]] | None:
    """Token ids for the whole conversation, with labels only on the assistant reply."""
    prompt_text = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, tokenize=False)
    full_text = tokenizer.apply_chat_template(messages, tokenize=False)
    if not full_text.startswith(prompt_text):
        raise ValueError("chat template does not render the prompt as a prefix of the conversation")
    prompt = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    full = tokenizer(full_text, add_special_tokens=False)["input_ids"]
    if full[: len(prompt)] != prompt:
        raise ValueError("tokenized prompt is not a prefix; refusing to mask the wrong training tokens")
    if len(full) <= len(prompt):
        raise ValueError("example has no assistant tokens to train on")
    if len(full) > max_length:
        return None
    labels = [-100] * len(prompt) + full[len(prompt):]
    return {"input_ids": full, "attention_mask": [1] * len(full), "labels": labels}


def training_arguments(wanted: dict) -> TrainingArguments:
    """Build TrainingArguments across transformers versions: options this version lacks are dropped
    (or renamed where an older spelling exists)."""
    from transformers import TrainingArguments

    known = {f.name for f in dataclasses.fields(TrainingArguments)}
    renames = {"eval_strategy": "evaluation_strategy", "use_cpu": "no_cuda"}
    accepted = {}
    for key, value in wanted.items():
        if key in known:
            accepted[key] = value
        elif renames.get(key) in known:
            accepted[renames[key]] = value
    return TrainingArguments(**accepted)


class ChatDataset:
    """Map-style dataset consumed through __len__ and __getitem__ by PyTorch."""
    def __init__(self, tokenizer, examples, max_length: int) -> None:
        self.items = [e for e in (encode(tokenizer, m, max_length) for m in examples) if e is not None]
        self.skipped = len(examples) - len(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> dict[str, list[int]]:
        return self.items[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", required=True, type=Path, help="training JSONL from `python -m loopos.admin export`")
    parser.add_argument("--val", type=Path, help="validation JSONL (`python -m loopos.admin export --val 0.1`)")
    parser.add_argument("--base", default="Qwen/Qwen2.5-Coder-1.5B-Instruct", help="Hugging Face model id or path")
    parser.add_argument("--out", type=Path, default=Path("train/out"))
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16, help="LoRA rank")
    parser.add_argument("--alpha", type=int, default=32, help="LoRA alpha")
    parser.add_argument("--max-length", type=int, default=4096, help="skip examples longer than this (tokens)")
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accum", type=int, default=8, help="gradient accumulation steps")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--limit", type=int, help="use only the first N examples (smoke tests)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--merge", action="store_true", help="also save the merged full model to OUT/merged")
    args = parser.parse_args(argv)
    for name in ("epochs", "lr", "rank", "alpha", "max_length", "batch", "accum", "threads"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and greater than zero")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")
    train_examples = read_examples(args.data, args.limit)
    val_examples = read_examples(args.val, args.limit) if args.val else None
    if not train_examples or (args.val and not val_examples):
        parser.error("training and any requested validation file must contain examples")

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer, DataCollatorForSeq2Seq, Trainer)

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    torch.set_num_threads(args.threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32

    print(f"base model   {args.base}")
    tokenizer = AutoTokenizer.from_pretrained(args.base)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.base, dtype=dtype)
    model.config.use_cache = False
    # Recompute activations in the backward pass: a 0.5B model on 4k-token examples otherwise needs
    # ~6 GB for activations alone, more than a small CPU VM has.
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    targets = [m for m in TARGET_MODULES if any(name.endswith(m) for name, _ in model.named_modules())]
    if not targets:
        targets = "all-linear"
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05,
                                             target_modules=targets, task_type="CAUSAL_LM"))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"lora         rank {args.rank} on {targets if isinstance(targets, str) else ', '.join(targets)}: "
          f"{trainable / 1e6:.2f}M of {total / 1e6:.1f}M parameters trainable")

    train_set = ChatDataset(tokenizer, train_examples, args.max_length)
    val_set = ChatDataset(tokenizer, val_examples, args.max_length) if val_examples is not None else None
    if not len(train_set):
        print("no usable training examples", file=sys.stderr)
        return 1
    if val_set is not None and not len(val_set):
        print("no usable validation examples within --max-length", file=sys.stderr)
        return 1
    print(f"examples     {len(train_set)} train" + (f", {len(val_set)} validation" if val_set else "")
          + (f" ({train_set.skipped} skipped as longer than {args.max_length} tokens)" if train_set.skipped else ""))

    total_steps = math.ceil(len(train_set) / (args.batch * args.accum)) * math.ceil(args.epochs)
    arguments = dict(
        output_dir=str(args.out / "checkpoints"), per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch, gradient_accumulation_steps=args.accum,
        num_train_epochs=args.epochs, learning_rate=args.lr, lr_scheduler_type="cosine",
        warmup_steps=max(1, total_steps // 20), logging_steps=1, save_strategy="steps", save_steps=5,
        save_total_limit=2, report_to=[],
        seed=args.seed, use_cpu=device == "cpu", bf16=device == "cuda", dataloader_num_workers=0,
        remove_unused_columns=False, eval_strategy="no",
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
    )
    trainer = Trainer(model=model, args=training_arguments(arguments), train_dataset=train_set,
                      eval_dataset=val_set, data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True))
    baseline = trainer.evaluate() if val_set else None
    if baseline:
        trainer.save_metrics("baseline", baseline)
        print(f"baseline     validation loss {baseline['eval_loss']:.3f}", flush=True)
    started = time.time()
    result = trainer.train()
    if not math.isfinite(result.training_loss):
        raise ValueError("training produced non-finite loss; refusing to save an invalid model")
    trainer.save_metrics("train", result.metrics)
    print(f"trained      loss {result.training_loss:.3f} in {time.time() - started:.0f}s on {device}")
    if val_set:
        evaluation = trainer.evaluate()
        if not math.isfinite(evaluation["eval_loss"]):
            raise ValueError("validation produced non-finite loss; refusing to save an invalid model")
        trainer.save_metrics("eval", evaluation)
        print(f"validation   loss {evaluation['eval_loss']:.3f}")

    adapter = args.out / "adapter"
    model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)
    print(f"adapter      {adapter}")
    report = {"base": args.base, "train_examples": len(train_set),
              "validation_examples": len(val_set) if val_set else 0,
              "epochs": args.epochs, "training_loss": result.training_loss,
              "baseline_validation_loss": baseline["eval_loss"] if baseline else None,
              "validation_loss": evaluation["eval_loss"] if val_set else None,
              "runtime_seconds": round(time.time() - started, 1),
              "dataset_note": "Assess data provenance and held-out task performance before claiming model improvement."}
    (args.out / "training-report.json").write_text(json.dumps(report, indent=2) + "\n")
    if args.merge:
        merged_dir = args.out / "merged"
        merged = model.merge_and_unload()
        merged.save_pretrained(merged_dir, safe_serialization=True)
        tokenizer.save_pretrained(merged_dir)
        print(f"merged       {merged_dir}   next: train/to_ollama.sh {merged_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

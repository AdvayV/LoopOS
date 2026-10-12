"""LoRA fine-tune a small chat model on LoopOS traces. Runs on CPU; a GPU is used if present.

    python train/finetune.py --data loopos-dataset.jsonl --val loopos-dataset.val.jsonl \\
        --base Qwen/Qwen2.5-Coder-1.5B-Instruct --out train/out --merge

Each example is {"messages": [system, user, assistant]} as written by `loopos export`. Only the
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

import torch
from peft import LoraConfig, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer, DataCollatorForSeq2Seq, Trainer,
                          TrainingArguments)

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def read_examples(path: Path, limit: int | None = None) -> list[list[dict[str, str]]]:
    examples = []
    with open(path) as lines:
        for line in lines:
            if line.strip():
                examples.append(json.loads(line)["messages"])
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
        # The reply changed how the boundary tokenizes; mask by the prompt's length instead.
        pass
    if len(full) > max_length:
        return None
    labels = [-100] * len(prompt) + full[len(prompt):]
    return {"input_ids": full, "attention_mask": [1] * len(full), "labels": labels}


def training_arguments(wanted: dict) -> TrainingArguments:
    """Build TrainingArguments across transformers versions: options this version lacks are dropped
    (or renamed where an older spelling exists)."""
    known = {f.name for f in dataclasses.fields(TrainingArguments)}
    renames = {"eval_strategy": "evaluation_strategy", "use_cpu": "no_cuda"}
    accepted = {}
    for key, value in wanted.items():
        if key in known:
            accepted[key] = value
        elif renames.get(key) in known:
            accepted[renames[key]] = value
    return TrainingArguments(**accepted)


class ChatDataset(torch.utils.data.Dataset):
    def __init__(self, tokenizer, examples, max_length: int) -> None:
        self.items = [e for e in (encode(tokenizer, m, max_length) for m in examples) if e is not None]
        self.skipped = len(examples) - len(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> dict[str, list[int]]:
        return self.items[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", required=True, type=Path, help="training JSONL from `loopos export`")
    parser.add_argument("--val", type=Path, help="validation JSONL (`loopos export --val 0.1`)")
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
    targets = [m for m in TARGET_MODULES if any(name.endswith(m) for name, _ in model.named_modules())]
    if not targets:
        targets = "all-linear"
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05,
                                             target_modules=targets, task_type="CAUSAL_LM"))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"lora         rank {args.rank} on {targets if isinstance(targets, str) else ', '.join(targets)}: "
          f"{trainable / 1e6:.2f}M of {total / 1e6:.1f}M parameters trainable")

    train_set = ChatDataset(tokenizer, read_examples(args.data, args.limit), args.max_length)
    val_set = ChatDataset(tokenizer, read_examples(args.val, args.limit), args.max_length) if args.val else None
    if not len(train_set):
        print("no usable training examples", file=sys.stderr)
        return 1
    print(f"examples     {len(train_set)} train" + (f", {len(val_set)} validation" if val_set else "")
          + (f" ({train_set.skipped} skipped as longer than {args.max_length} tokens)" if train_set.skipped else ""))

    total_steps = math.ceil(len(train_set) / (args.batch * args.accum)) * math.ceil(args.epochs)
    arguments = dict(
        output_dir=str(args.out / "checkpoints"), per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch, gradient_accumulation_steps=args.accum,
        num_train_epochs=args.epochs, learning_rate=args.lr, lr_scheduler_type="cosine",
        warmup_steps=max(1, total_steps // 20), logging_steps=5, save_strategy="no", report_to=[],
        seed=args.seed, use_cpu=device == "cpu", bf16=device == "cuda", dataloader_num_workers=0,
        remove_unused_columns=False, eval_strategy="epoch" if val_set else "no",
    )
    trainer = Trainer(model=model, args=training_arguments(arguments), train_dataset=train_set,
                      eval_dataset=val_set, data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True))
    started = time.time()
    result = trainer.train()
    print(f"trained      loss {result.training_loss:.3f} in {time.time() - started:.0f}s on {device}")
    if val_set:
        print(f"validation   loss {trainer.evaluate()['eval_loss']:.3f}")

    adapter = args.out / "adapter"
    model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)
    print(f"adapter      {adapter}")
    if args.merge:
        merged_dir = args.out / "merged"
        merged = model.merge_and_unload()
        merged.save_pretrained(merged_dir, safe_serialization=True)
        tokenizer.save_pretrained(merged_dir)
        print(f"merged       {merged_dir}   next: train/to_ollama.sh {merged_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

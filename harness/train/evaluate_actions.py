"""Compare guided syscall generation before/after training; never execute model output."""
from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from loopos.prompt import SYSTEM
from loopos.syscalls import SyscallError, validate
from loopos.worker import parse_action


CASES = [
    ("list_dir", "List the workspace root directory. Use path '.' in your action.", {"path": "."}),
    ("read_file", "Read the existing file calc.py before changing it.", {"path": "calc.py"}),
    ("write_file", "Create notes.txt containing exactly hello (no newline).", {"path": "notes.txt", "content": "hello"}),
    ("edit_file", "In calc.py replace the unique exact text 'return a - b' with 'return a + b'.",
     {"path": "calc.py", "find": "return a - b", "replace": "return a + b"}),
    ("run", "Run the allowed command pytest -q to inspect the current tests.", {"command": "pytest -q"}),
    ("finish", "The supervisor's done check just passed with exit 0. Request completion.", {}),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    reports = []
    for name in args.models:
        print(f"Loading {name}", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(name)
        model = AutoModelForCausalLM.from_pretrained(name, dtype=torch.float32)
        model.eval()
        rows = []
        for expected, goal, fields in CASES:
            messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": goal}]
            inputs = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt",
                                                   return_dict=True)
            started = time.monotonic()
            with torch.inference_mode():
                output = model.generate(**inputs, max_new_tokens=192, do_sample=False,
                                        pad_token_id=tokenizer.eos_token_id)
            reply = tokenizer.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
            action, error = parse_action(reply)
            normalized = None
            if action is not None:
                try:
                    normalized = validate(action)
                except SyscallError as exc:
                    error = str(exc)
            correct = bool(normalized and normalized["action"] == expected
                           and all(normalized.get(k) == v for k, v in fields.items()))
            rows.append({"expected_action": expected, "valid_request": normalized is not None,
                         "matches_guided_request": correct, "reply": reply, "error": error,
                         "seconds": round(time.monotonic() - started, 2)})
            print(f"  {expected}: valid={normalized is not None}, exact_fields={correct}", flush=True)
        reports.append({"model": name, "cases": rows, "valid_requests": sum(r["valid_request"] for r in rows),
                        "matched_requests": sum(r["matches_guided_request"] for r in rows), "total": len(rows)})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"scope": "six guided format checks, not autonomous task accuracy",
                                        "reports": reports}, indent=2) + "\n")
        del model, tokenizer
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Training your own LoopOS worker model

The loop traces LoopOS records are the dataset; this folder turns them into a fine-tuned model
that is better at acting inside the harness, then loads it into Ollama so loops can use it.

```
loops run ──► trace.jsonl ──► loopos export ──► finetune.py (LoRA) ──► to_ollama.sh ──► loopos model NAME
```

## 1. Collect traces

```bash
loopos tasks examples/* --holdout 3        # practice tasks per project, plus held-out ones
loopos bench inventory-tasks textstats-tasks ledger-tasks grid-tasks queue-tasks
```

Every iteration of every loop is appended to `$LOOPOS_HOME/loops/<id>/trace.jsonl`. Run the
benchmark more than once (different models, more projects) to collect more. Loops run on your own
projects count too.

## 2. Export a dataset

```bash
loopos export loopos-dataset.jsonl --val 0.1
```

Keeps iterations from loops that reached done plus any step that raised the score, drops refused
and invalid replies, removes duplicates, and sets 10% of loops aside in `loopos-dataset.val.jsonl`.
Each line is `{"messages": [system, user, assistant], "meta": {...}}`.

## 3. Fine-tune

```bash
python -m venv train/.venv && train/.venv/bin/pip install -r train/requirements.txt
train/.venv/bin/python train/finetune.py --data loopos-dataset.jsonl --val loopos-dataset.val.jsonl \
    --base Qwen/Qwen2.5-Coder-1.5B-Instruct --out train/out --merge
```

LoRA adapters (rank 16) on the attention and MLP projections; only the assistant reply is trained
on. On a CPU VM a 1.5B model trains at roughly a few seconds per example per epoch, so a few
thousand examples take hours, not days; 3B is about twice that. Useful flags: `--epochs`, `--lr`,
`--rank`, `--max-length` (skip long examples), `--limit N` (smoke test), `--threads`.

On the VM: `loopos-vm push loopos-dataset*.jsonl`, run the command above there with
`loopos-vm busy on` so it is not shut down as idle, then `loopos-vm fetch train/out`.

## 4. Load into Ollama and compare

```bash
train/to_ollama.sh train/out/merged loopos-worker      # GGUF via llama.cpp, quantized q4_K_M
loopos model loopos-worker
loopos bench inventory-holdout textstats-holdout        # never seen in training
loopos model qwen2.5-coder:1.5b && loopos bench inventory-holdout textstats-holdout
```

The model is better if it solves more held-out tasks in fewer iterations than the base model.
Repeat from step 1 with the new model: better traces train the next version.

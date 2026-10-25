# Training your own LoopOS worker model

The loop traces LoopOS records are the dataset; this folder turns them into a fine-tuned model
that is better at acting inside the harness, then loads it into Ollama so loops can use it.

```
loops run ──► trace.jsonl ──► python -m loopos.admin export ──► finetune.py (LoRA) ──► to_ollama.sh ──► python -m loopos.admin model NAME
```

Training tools run through `python -m loopos.admin` from the installed harness environment.
The public `loopos` executable has only the three syscall, status, and result forms.

For Azure credits, see [the Azure trainer setup](../../infra/azure/README.md).

If existing model traces contain no successful steps, collect explicitly scripted teaching traces:

```bash
python train/run_once.py --out train/out/seed-job --max-seconds 600 -- \
    python train/collect_seed.py --out train/out/seed-data --per-project 5
```

This runs verified repairs on planted bugs and exports a task-separated train/validation split.
Its report labels the source as scripted oracle demonstrations. Separate held-out bug tasks are
created alongside the teaching tasks. These traces teach the protocol and repairs; they do not
establish a model's autonomous performance. Premature `finish` requests that fail the check are
excluded from the useful training export, even if a later step solves the loop.

## 1. Collect traces

```bash
python -m loopos.admin tasks examples/* --holdout 3        # practice tasks per project, plus held-out ones
python -m loopos.admin bench inventory-tasks textstats-tasks ledger-tasks grid-tasks queue-tasks
```

Every iteration of every loop is appended to `$LOOPOS_HOME/loops/<id>/trace.jsonl`. Run the
benchmark more than once (different models, more projects) to collect more. Loops run on your own
projects count too.

## 2. Export a dataset

```bash
python -m loopos.admin export loopos-dataset.jsonl --val 0.1
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

For one bounded attempt with progress every 30 seconds, use the job runner:

```bash
python train/run_once.py --out train/out/job --max-seconds 7200 --update-seconds 30 -- \
    train/.venv/bin/python -u train/finetune.py \
    --data loopos-dataset.jsonl --val loopos-dataset.val.jsonl \
    --base Qwen/Qwen2.5-Coder-1.5B-Instruct --out train/out/model --epochs 2 --merge
```

The runner writes `training-status.json` and `training.log` in the job directory. It locks the
directory against concurrent launches, refuses a previously attempted job unless `--restart`
is explicitly supplied, and never retries automatically. The deadline covers the whole command,
including model downloads; a timeout kills the process group and returns 124. Ctrl+C/SIGTERM
also cleans up the process group. A successful wrapper exit means the command completed; assess
model quality on held-out tasks separately. The runner does not launch or shut down AWS resources.

LoRA adapters (rank 16) on the attention and MLP projections; only the assistant reply is trained
on. CPU time varies substantially with sequence length and hardware; measure initial updates
before estimating the whole run. The four-vCPU Azure setup retains the 1.5B base and allows a
longer bounded run. Useful flags: `--epochs`, `--lr`,
`--rank`, `--max-length` (skip long examples), `--limit N` (smoke test), `--threads`.

On the VM: `loopos-vm push loopos-dataset*.jsonl`, run the command above there with
`loopos-vm busy on` so it is not shut down as idle, then `loopos-vm fetch train/out`.

## 4. Load into Ollama and compare

```bash
train/to_ollama.sh train/out/merged loopos-worker      # GGUF via llama.cpp, quantized q4_K_M
python -m loopos.admin model loopos-worker
python -m loopos.admin bench inventory-holdout textstats-holdout        # never seen in training
python -m loopos.admin model qwen2.5-coder:1.5b && python -m loopos.admin bench inventory-holdout textstats-holdout
```

The model is better if it solves more held-out tasks in fewer iterations than the base model.
Repeat from step 1 with the new model: better traces train the next version.

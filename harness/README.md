# LoopOS harness (CLI)

Start a loop with a goal and a done check, then walk away. A worker process asks a model for one action at a time; the supervisor checks and performs each action, runs the done check itself, and keeps the loop on track until the check passes or a budget runs out.

Type `loopos` on its own for the interactive prompt under the LoopOS logo, or use the short commands directly:

```bash
loopos run                    # in a project folder: goal and done check are worked out
loopos run myproject          # another folder
loopos run "fix the login"    # with a goal
loopos ps                     # loops at a glance
loopos watch l1               # follow one live
loopos stop l1                # pause l1 / resume l1 / show l1 / logs l1 work the same way
loopos model qwen2.5-coder:7b # choose the model once; it is remembered
```

The done check is detected from the project: `pytest -q` for Python tests, `npm test`, `make test`, `cargo test` or `go test ./...`; pass `-u CMD` for anything else. Options such as `-p 8` (priority), `-n 10` (iterations) and `-d` (start and return) are there when needed.

`run` then shows the loop live, one line per iteration as it completes, with a spinner for what it is doing now:

```
  ◆ l1  Fix the bug so the tests pass
    until pytest -q  ·  qwen2.5-coder:3b  ·  priority 5
    in    ~/myproject

  start  ✗ failing · 10/11 tests
    1  ✗ run    pytest -q                           exit 1                ━━━━━━━─ 0.91
    2  ✓ read   inventory.py                                              ━━━━━━━─ 0.91
    3  ✓ edit   inventory.py                                              ━━━━━━━━ 1.00  11/11 tests  ★ best

  ✔ done · check passed · 3 iterations · 6s
```

Ctrl+C detaches (the loop keeps running); `loopos watch l1` picks it up again. `loopos ps` lists loops, `loopos top` is a live view of all of them. Colours follow the terminal and are off when `NO_COLOR` is set.

## Install

Python 3.11+ on Linux. No third-party dependencies.

```bash
cd harness
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/loopos --help        # or: python -m loopos --help
```

The worker model is served by [Ollama](https://ollama.com) (`--provider ollama`, default `http://localhost:11434`): locally (`ollama pull qwen2.5-coder:7b`), or on the CPU VM through `loopos-vm tunnel` (see `../infra/aws`). Any OpenAI-compatible server also works with `--provider openai --model-url http://host:port/v1`: llama.cpp's `llama-server`, vLLM or LM Studio. A key, if needed, is read from `$LOOPOS_API_KEY`.

## How it works

```
loopos (CLI) ──unix socket──► supervisor (daemon, "kernel")
                                 │  spawns, schedules, checks, restarts
                                 ▼
                     worker process per loop ──► model (Ollama)
```

Each iteration: the worker waits for a **model turn**, asks the model for one JSON action, sends it to the supervisor as a **syscall**, and reports the iteration. The supervisor runs the **done check** and replies `continue` or `stop`.

| OS idea | In LoopOS |
|---|---|
| Process | Each loop's worker is a real child process in its own session (`ps`, `loopos show` print its pid). |
| Scheduling | One shared model; turns go to the highest `priority + waited_turns // 3`, ties to the longest waiter. Switches happen only between iterations. |
| Signals | `loopos pause` sends `SIGSTOP` (deferred to the end of a model turn), `resume` sends `SIGCONT`, `kill` sends `SIGKILL`. |
| System calls | Workers never touch files. `list_dir`, `read_file`, `write_file`, `edit_file`, `run` and `finish` are requests the supervisor validates: paths must stay inside the workspace, `.git/` is read-only, and only allowed programs run, without a shell. |
| Isolation | Commands and workers start through `loopos.sandbox`: `RLIMIT_CPU`, `RLIMIT_AS`, `nice` by priority, and **Landlock**, so a command cannot write outside the workspace and the temp dir, and (Linux 6.7+) cannot open or accept TCP connections unless the loop was started with `--net`. |
| Watchdog | Crashed workers are restarted from their saved state; hung ones (no progress for 15 min) are killed and restarted, up to 3 times. |
| Livelock | The same action with the same result 3 times, or cycling between two actions without changing the check, or 8 iterations without a new best: the supervisor restores the best checkpoint and tells the model to change approach. After 2 recoveries it gives up. |
| Checkpoints | The workspace is snapshotted at the baseline and at every new best score. Unsuccessful loops end with the workspace at the best checkpoint. |
| Recovery | Stopping the supervisor interrupts loops; starting it again resumes them from their last saved iteration. |

Scores come from the done check: exit 0 is 1.0, a pytest summary gives the pass fraction, anything else is 0.

## Commands

| Command | |
|---|---|
| `loopos` | Interactive prompt: the same commands without the `loopos` prefix; `help`, `clear`, `exit`. |
| `loopos run [DIR] ["GOAL"]` | Start a loop and watch it. `-u CMD` check, `-p N` priority, `-n N` iterations, `-d` detach, `-m MODEL`. |
| `loopos watch ID` | Follow a loop live. |
| `loopos ps [-a]` · `loopos top` · `loopos show ID` · `loopos logs ID` | Inspect loops (`logs --raw` for every supervisor event). |
| `loopos pause ID` · `resume ID` · `stop ID` | Control a loop (`SIGSTOP`, `SIGCONT`, `SIGKILL`). |
| `loopos model [NAME]` · `loopos config [KEY VALUE]` | Show or set remembered defaults: model, provider, url, priority, iters. |
| `loopos tasks DIR [DIR...]` | Plant bugs in passing projects → `DIR-tasks/` (`-n` count, `--holdout N` for a separate `DIR-holdout/`). |
| `loopos bench DIR [DIR...]` | Score the model on those tasks (`-j` loops at once). |
| `loopos export [FILE] [--val 0.1]` | Write a fine-tuning dataset from loop traces, optionally with a validation split. |
| `loopos daemon start\|stop\|status` | Manage the supervisor (normally automatic). |

Settings come from, in order: a flag, an environment variable (`LOOPOS_MODEL`, `LOOPOS_PROVIDER`, `LOOPOS_MODEL_URL`, `LOOPOS_PRIORITY`), `loopos config`, then the built-in default.

State lives in `$LOOPOS_HOME` (default `~/.local/state/loopos`): `loopos.db` (loops and events), and `loops/<id>/` with `spec.json`, `state.json`, `trace.jsonl`, `worker.log` and `checkpoints/`.

Without a model, `--script replies.json` replays a JSON list of actions instead (`--delay` adds thinking time, or set `LOOPOS_SCRIPT`); this is what the tests use. `examples/demo.sh` tours every feature this way.

## Practice tasks and benchmarks

```bash
loopos tasks examples/* --holdout 3    # -> inventory-tasks/ + inventory-holdout/, textstats-tasks/ ...
loopos bench inventory-tasks textstats-tasks ledger-tasks grid-tasks queue-tasks
```

`examples/` holds five small Python projects with passing tests (stock keeping, text utilities, a ledger, grid algorithms, a priority job queue), together good for a few hundred distinct planted bugs. Held-out tasks use different bugs from the same project, so a model trained on the main set can be measured on them.

`tasks` swaps an operator, flips a comparison, swaps `and`/`or`, or shifts an integer by one, and keeps the task only if the tests now fail. The manifest (`tasks.json`, outside the task folders) records each bug and a hash of every test file. `bench` copies each task to `$LOOPOS_HOME/bench/<time>/`, runs loops (sharing the model through the scheduler), counts a task as solved only if the check passes **and** the tests are unchanged, and writes `results.json`. Use it to compare models, and to generate traces for training.

Loop commands find `python` and `pytest` in the workspace's `.venv/bin` first, then next to the interpreter running LoopOS.

## Training data and your own model

Every iteration is appended to `loops/<id>/trace.jsonl` as `{"messages": [system, user, assistant], ...}` with the score before and after. `loopos export` keeps steps from loops that reached done, plus any step that raised the score, drops refused or invalid replies, removes duplicates, and with `--val 0.1` sets a tenth of the loops aside for validation. `train/` turns that dataset into a LoRA fine-tune of a small model and loads it into Ollama; see [train/README.md](train/README.md).

## Tests

```bash
.venv/bin/python -m pytest -q
```

Integration tests start a real supervisor and worker processes and cover: reaching done, refused actions, verified `finish`, livelock recovery and give-up with rollback, budgets, crash restart, hang detection, pause/resume with `SIGSTOP`, kill, an unreachable model, supervisor restart recovery, commands reached by pause and kill, task generation, benchmarking (including test tampering), and dataset export.

## Limits

- Landlock confines writes and TCP, not reads: an allowed interpreter such as `python` can still read any file the user can. Run loops on the VM or in a container for untrusted goals.
- One model turn at a time (`slots=1`); a larger VM could serve several.

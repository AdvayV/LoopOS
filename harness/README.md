# LoopOS harness (CLI)

Start a loop with a goal and a done check, then walk away. A worker process asks a model for one action at a time; the supervisor checks and performs each action, runs the done check itself, and keeps the loop on track until the check passes or a budget runs out.

```bash
loopos run --goal "make the tests pass" --until "pytest -q" --workspace ./myproject
loopos ps            # loops and their progress
loopos top           # live view, including scheduler decisions
loopos logs l1 -f    # every action, check, recovery and restart
```

## Install

Python 3.11+ on Linux. No third-party dependencies.

```bash
cd harness
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/loopos --help        # or: python -m loopos --help
```

The worker model is served by [Ollama](https://ollama.com): locally (`ollama pull qwen2.5-coder:7b`), or on the GPU VM through `loopos-vm tunnel` (see `../infra/aws`). Either way it is reached at `http://localhost:11434`.

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
| Isolation | Commands and workers start through `loopos.sandbox`: `RLIMIT_CPU`, `RLIMIT_AS`, `nice` by priority, and **Landlock**, so a command cannot write outside the workspace and the temp dir. |
| Watchdog | Crashed workers are restarted from their saved state; hung ones (no progress for 15 min) are killed and restarted, up to 3 times. |
| Livelock | The same action with the same result 3 times, or cycling between two actions without changing the check, or 8 iterations without a new best: the supervisor restores the best checkpoint and tells the model to change approach. After 2 recoveries it gives up. |
| Checkpoints | The workspace is snapshotted at the baseline and at every new best score. Unsuccessful loops end with the workspace at the best checkpoint. |
| Recovery | Stopping the supervisor interrupts loops; starting it again resumes them from their last saved iteration. |

Scores come from the done check: exit 0 is 1.0, a pytest summary gives the pass fraction, anything else is 0.

## Commands

| Command | |
|---|---|
| `loopos run --goal G --until CMD [--workspace DIR] [--priority 0-10] [--max-iters N] [--max-minutes M] [--model NAME] [--allow PROGRAM] [-f]` | Start a loop (starts the supervisor if needed). |
| `loopos ps [-a]`, `loopos top`, `loopos show ID` | Inspect loops. |
| `loopos logs [ID] [-f] [-n N]` | Event log. |
| `loopos pause ID`, `resume ID`, `kill ID` | Control a loop. |
| `loopos daemon start\|stop\|status` | Manage the supervisor. |
| `loopos export [--out FILE] [--mode success\|all]` | Write a fine-tuning dataset from loop traces. |

State lives in `$LOOPOS_HOME` (default `~/.local/state/loopos`): `loopos.db` (loops and events), and `loops/<id>/` with `spec.json`, `state.json`, `trace.jsonl`, `worker.log` and `checkpoints/`.

Without a model, `--provider scripted --script replies.json` replays a JSON list of actions; this is what the tests use.

## Training data

Every iteration is appended to `loops/<id>/trace.jsonl` as `{"messages": [system, user, assistant], ...}` with the score before and after. `loopos export` keeps steps from loops that reached done, plus any step that raised the score, drops refused or invalid replies, and removes duplicates.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Integration tests start a real supervisor and worker processes and cover: reaching done, refused actions, verified `finish`, livelock recovery and give-up with rollback, budgets, crash restart, hang detection, pause/resume with `SIGSTOP`, kill, an unreachable model, supervisor restart recovery, and dataset export.

## Limits

- Landlock confines writes, not reads, and an allowed interpreter such as `python` can still read any file the user can. Run loops on the VM or in a container for untrusted goals.
- One model turn at a time (`slots=1`); a larger GPU could serve several.

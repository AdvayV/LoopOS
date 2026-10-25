# LoopOS harness

LoopOS exposes exactly three public command forms:

```bash
loopos <syscall> [arguments]
loopos check_status [ID]
loopos show results [ID]
```

Use `loopos --help` for syntax. A syscall executes immediately and prints its call ID and final
status. `check_status` reports the most recently started call, or the selected ID, including its
workspace and timing. `show results` explicitly prints that call’s request, status, exit code
when applicable, and saved output. Failed and refused calls are recorded too. Status and results
can be read from another terminal while a call runs; unfinished results are marked pending.
Results survive terminal restarts. Ctrl+C or SIGTERM stops command execution and records an
interrupted result. A later status lookup detects executors that died before saving a result.

## Install

Python 3.11+ on Linux. The public CLI requires no third-party dependencies.

```bash
cd harness
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/loopos --help
```

## Syscalls

| Syscall | Example | Behavior |
|---|---|---|
| `list_dir` | `loopos list_dir .` | List up to 200 entries; omit generated/cache directories. |
| `read_file` | `loopos read_file notes.txt` | Read up to 20,000 characters, with a truncation notice. |
| `write_file` | `loopos write_file notes.txt "hello"` | Create or overwrite a file; create parent directories. |
| `edit_file` | `loopos edit_file notes.txt hello goodbye` | Replace exactly one matching occurrence. |
| `run` | `loopos run python -c "print('hello')"` | Execute an allowed program, capturing stdout and stderr. |
| `finish` | `loopos finish --until "pytest -q"` | Execute a required done check and report whether it passed. |

These are the six **LoopOS harness syscalls**, rather than the host Linux syscall table.

```bash
loopos write_file greeting.txt "hello 世界"
loopos check_status
loopos show results
loopos read_file greeting.txt
loopos show results
loopos run python -c "print('actual command output')"
loopos show results
loopos show results 1                    # look up an earlier call
loopos run --timeout 5 python -c "import time; time.sleep(20)"
loopos show results                      # timeout and captured output
```

Each syscall accepts `-w DIR` / `--workspace DIR` (default: current directory) and
`--timeout SECONDS` (default: 120). Put these options before the program in `run`.
Use `--` before program arguments if necessary. Both a single quoted command and separate
program/argument tokens work. No shell is invoked: shell operators are literal arguments.

State lives in `$LOOPOS_HOME` (default `~/.local/state/loopos`), with direct calls stored in
`calls.db`. Override it with `loopos --home DIR ...`. Return codes are 0 for successful calls,
2 for refused requests or invalid syntax, 124 for timeouts, and 130 for interrupted calls;
executed programs preserve their own exit code. Status/result lookups return 1 when the ID
is missing or results are pending. Retrieving a completed failed result returns 0 because the
lookup succeeded; the printed status and exit code describe the execution failure.

## Execution limits

Paths must stay inside the workspace, including resolved symlinks. File writes protect `.git/`
and `.loopos/` at any depth. Reads are bounded, edits require one match, and written content
is limited to 200,000 characters. Command output retains the last 4,000 characters and reports
truncation. Commands use CPU/memory limits and, where available, Linux Landlock for filesystem
writes and TCP networking. Timeouts and interruptions kill the command process group.

The default allowed programs are `python`, `python3`, `pytest`, `ls`, `cat`, `head`, `tail`,
`grep`, `wc`, `find`, `diff`, `echo`, `true`, `sort`, and `uniq`. Commands can write within the
workspace and temporary directory. Landlock does not restrict reads by allowed interpreters,
and allowed programs may themselves mutate files, so use a VM/container for untrusted code.
These limits are not a complete security boundary.

## Model training and supervisor tooling

The model worker and supervisor remain available to collect training traces and benchmark
models. Their maintenance commands use a separate internal Python module:

```bash
.venv/bin/python -m loopos.admin --help
.venv/bin/python -m loopos.admin tasks examples/inventory --holdout 3
.venv/bin/python -m loopos.admin bench inventory-tasks
.venv/bin/python -m loopos.admin export loopos-dataset.jsonl --val 0.1
```

This keeps training, model configuration, benchmarking, and daemon management out of the public
three-form CLI. See [the training guide](train/README.md) and [AWS VM guide](../infra/aws/README.md).
The worker requests one JSON action per iteration; the supervisor validates it, executes it,
and checks completion. It provides model-turn scheduling, worker restart, livelock detection,
budgets, best-checkpoint rollback, and trace recording. Loop state remains in `loopos.db` and
`loops/<id>/`, separate from public syscall history.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python tests/terminal_smoke.py
```

The tests cover all six syscalls, explicit results, historical and live status, independent
terminal processes, Unicode, empty/binary files, malformed requests, refusals, failed commands,
timeouts, signals, abandoned calls, path/symlink escapes, protected directories, output/content
limits, and ambiguous edits. Supervisor tests cover scheduling, verified completion, recovery,
budgets, pause/resume/kill, benchmarking, and dataset export. The terminal smoke test prints a
readable walkthrough, checks real output, and saves a transcript in `train/out/terminal-smoke/`.

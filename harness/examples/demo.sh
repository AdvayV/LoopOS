#!/bin/bash
# A tour of the LoopOS CLI without a model: the scripted provider replays actions, everything else is
# real (supervisor daemon, worker processes, sandboxed commands, done checks, watchdog, scheduler).
#   harness/examples/demo.sh            (state goes to a fresh temporary LOOPOS_HOME)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
HARNESS=$(dirname "$HERE")
BIN=${LOOPOS_BIN:-$HARNESS/.venv/bin}
export LOOPOS_HOME=${LOOPOS_HOME:-$(mktemp -d /tmp/loopos-demo-XXXX)}
export PATH="$BIN:$PATH"
WORK=$LOOPOS_HOME/work
mkdir -p "$WORK"
cd "$WORK"

say() { printf '\n  \033[2m%s\033[0m\n' "$*"; }
show() {  # print the command like a shell would, wrapped with \ at ~90 columns
  local line="loopos" out="" unit args=("$@") i=0
  while (( i < ${#args[@]} )); do
    unit=${args[i]}; [[ $unit == *" "* ]] && unit="\"$unit\""
    # keep "--flag value" together when wrapping
    if [[ $unit == --* && $(( i + 1 )) -lt ${#args[@]} && ${args[i+1]} != -* ]]; then
      local value=${args[i+1]}; [[ $value == *" "* ]] && value="\"$value\""
      unit+=" $value"; i=$(( i + 1 ))
    fi
    if (( ${#line} + ${#unit} > 88 )); then out+="$line \\"$'\n'"      "; line="$unit"; else line+=" $unit"; fi
    i=$(( i + 1 ))
  done
  printf '\n  \033[2m$\033[0m %s\n' "$out$line"
  sleep "${DEMO_PAUSE:-0.6}"
}
loopos() { show "$@"; command loopos "$@" || true; }  # a loop that gives up exits 1; keep touring

python -m loopos.ui

say "Plant a bug in a passing project, four different ways"
cp -r "$HERE/inventory" project
loopos tasks make --from project --out tasks --count 4

# Scripted "model" replies, built from the manifest so each fix matches its planted bug.
SOURCE=project/inventory.py python3 - <<'PY'
import json, os
from pathlib import Path
tasks = json.loads(Path("tasks/tasks.json").read_text())
def fix(task):
    m = task["mutation"]
    line = Path("tasks", task["name"], m["path"]).read_text().splitlines()[m["line"] - 1]
    good = line[:m["col"]] + m["old"] + line[m["col"] + len(m["new"]):]
    return [{"action": "run", "command": "pytest -q", "reason": "see what fails"},
            {"action": "read_file", "path": m["path"], "reason": "inspect the code under test"},
            {"action": "edit_file", "path": m["path"], "find": line, "replace": good, "reason": "fix the bug"}]
Path("fix.json").write_text(json.dumps(fix(tasks[0])))
Path("stuck.json").write_text(json.dumps([{"action": "read_file", "path": "inventory.py", "reason": "look again"}]))
Path("escape.json").write_text(json.dumps(
    [{"action": "read_file", "path": "../../../etc/passwd", "reason": "curious"},
     {"action": "run", "command": "curl https://example.com", "reason": "download a fix"}] + fix(tasks[2])))
Path("restore.json").write_text(json.dumps([{"action": "write_file", "path": "inventory.py",
                                             "content": Path(os.environ["SOURCE"]).read_text(), "reason": "restore"}]))
PY
cp -r tasks/task-001 fixer; cp -r tasks/task-001 stuck; cp -r tasks/task-003 escaper

say "One loop, watched live: each iteration appears as it happens"
loopos run --goal "Fix the bug so the tests pass" --until "pytest -q" --workspace fixer \
    --provider scripted --script fix.json --script-delay 1

say "Two loops sharing the model: one keeps repeating itself, one tries to leave its folder"
loopos run -d --goal "Fix the bug so the tests pass" --until "pytest -q" --workspace stuck --priority 2 \
    --provider scripted --script stuck.json --script-delay 1
loopos run -d --goal "Fix the bug so the tests pass" --until "pytest -q" --workspace escaper --priority 5 \
    --provider scripted --script escape.json --script-delay 1
loopos watch l3
loopos watch l2
loopos ps -a

say "Benchmark: a loop per task, two at a time"
loopos bench tasks --parallel 2 --provider scripted --script restore.json --script-delay 0.5

say "Every iteration becomes training data"
loopos export --out dataset.jsonl
loopos daemon stop >/dev/null
printf '\n  \033[2mstate kept in %s\033[0m\n\n' "$LOOPOS_HOME"

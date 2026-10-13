#!/bin/bash
# A tour of the LoopOS CLI without a model: scripted replies stand in for the model (set through
# LOOPOS_SCRIPT, so the commands shown are exactly what you would type); everything else is real.
#   harness/examples/demo.sh            (state goes to a fresh temporary LOOPOS_HOME)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
HARNESS=$(dirname "$HERE")
export LOOPOS_HOME=${LOOPOS_HOME:-$(mktemp -d /tmp/loopos-demo-XXXX)}
export PATH="${LOOPOS_BIN:-$HARNESS/.venv/bin}:$PATH"
WORK=$LOOPOS_HOME/work
mkdir -p "$WORK"
cd "$WORK"

say() { printf '\n  \033[2m%s\033[0m\n' "$*"; }
loopos() {  # show the command, then run it (a loop that gives up exits 1; keep touring)
  printf '\n  \033[2m$\033[0m loopos %s\n' "$*"
  sleep "${DEMO_PAUSE:-0.6}"
  command loopos "$@" || true
}
replay() {  # replay SCRIPT PRIORITY loopos-args...: run with scripted model replies
  local script=$1 priority=$2
  shift 2
  LOOPOS_SCRIPT=$script LOOPOS_SCRIPT_DELAY=${DELAY:-1} LOOPOS_PRIORITY=$priority loopos "$@"
}

python -m loopos.ui
say "No model is connected, so this tour replays scripted model replies."

cp -r "$HERE/inventory" project
say "Plant bugs in a passing project"
loopos tasks project

# Scripted replies, built from the manifest so each fix matches its planted bug.
python3 - <<'PY'
import json
from pathlib import Path
tasks = json.loads(Path("project-tasks/tasks.json").read_text())
def fix(task):
    m = task["mutation"]
    line = Path("project-tasks", task["name"], m["path"]).read_text().splitlines()[m["line"] - 1]
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
                                             "content": Path("project/inventory.py").read_text(), "reason": "restore"}]))
PY
cp -r project-tasks/task-001 fixer; cp -r project-tasks/task-001 stuck; cp -r project-tasks/task-003 escaper

say "Run a loop on a folder: the goal and the check (pytest -q) are worked out for you"
replay fix.json 5 run fixer

say "Two loops share the model: one repeats itself, one tries to leave its folder"
replay stuck.json 2 run stuck -d
replay escape.json 5 run escaper -d
loopos watch l3
loopos watch l2
loopos ps -a

say "Score the model on the practice tasks"
DELAY=0.5 replay restore.json 5 bench project-tasks

say "Every iteration becomes training data"
loopos export
command loopos daemon stop >/dev/null
printf '\n  \033[2mstate kept in %s\033[0m\n\n' "$LOOPOS_HOME"

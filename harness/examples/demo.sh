#!/bin/bash
# A tour of the LoopOS CLI without a model: the scripted provider replays actions, everything else is
# real (supervisor daemon, worker processes, sandboxed commands, done checks, watchdog, scheduler).
#   harness/examples/demo.sh            (state goes to a fresh temporary LOOPOS_HOME)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
HARNESS=$(dirname "$HERE")
LOOPOS=${LOOPOS:-$HARNESS/.venv/bin/loopos}
export LOOPOS_HOME=${LOOPOS_HOME:-$(mktemp -d /tmp/loopos-demo-XXXX)}
WORK=$LOOPOS_HOME/work
mkdir -p "$WORK"
step() { printf '\n\033[1;36m━━ %s\033[0m\n' "$*"; }
run() { printf '\033[2m$ %s\033[0m\n' "$*"; "$@"; }
wait_all() { while "$LOOPOS" ps | grep -qE ' (running|thinking|waiting|paused) '; do sleep 1; done; }

step "1. Plant bugs in a passing project: practice tasks"
run "$LOOPOS" tasks make --from "$HERE/inventory" --out "$WORK/tasks" --count 4

# Scripted "model" replies, built from the manifest so each fix matches its planted bug.
python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path
work = Path(sys.argv[1])
tasks = json.loads((work / "tasks" / "tasks.json").read_text())
def fix(task):
    m = task["mutation"]
    line = (work / "tasks" / task["name"] / m["path"]).read_text().splitlines()[m["line"] - 1]
    good = line[:m["col"]] + m["old"] + line[m["col"] + len(m["new"]):]
    return [{"action": "run", "command": "python -m pytest -q -p no:cacheprovider", "reason": "see what fails"},
            {"action": "read_file", "path": m["path"], "reason": "inspect the code under test"},
            {"action": "edit_file", "path": m["path"], "find": line, "replace": good, "reason": "fix the bug"}]
for i, task in enumerate(tasks):
    (work / f"fix-{i + 1}.json").write_text(json.dumps(fix(task)))
read = {"action": "read_file", "path": "inventory.py", "reason": "look again"}
(work / "stuck.json").write_text(json.dumps([read]))
escape = [{"action": "read_file", "path": "../../../etc/passwd", "reason": "curious"},
          {"action": "run", "command": "curl https://example.com", "reason": "download a fix"}] + fix(tasks[2])
(work / "escape.json").write_text(json.dumps(escape))
PY
for t in 1 2 3; do cp -r "$WORK/tasks/task-00$t" "$WORK/loop$t"; done

step "2. Start three loops sharing one model (priorities 8, 2, 5; each reply takes 1.5s)"
run "$LOOPOS" run --goal "Fix the bug so the tests pass" --until "python -m pytest -q -p no:cacheprovider" \
    --workspace "$WORK/loop1" --priority 8 --provider scripted --script "$WORK/fix-1.json" --script-delay 1.5
run "$LOOPOS" run --goal "Fix the bug so the tests pass" --until "python -m pytest -q -p no:cacheprovider" \
    --workspace "$WORK/loop2" --priority 2 --provider scripted --script "$WORK/stuck.json" --script-delay 1.5
run "$LOOPOS" run --goal "Fix the bug so the tests pass" --until "python -m pytest -q -p no:cacheprovider" \
    --workspace "$WORK/loop3" --priority 5 --provider scripted --script "$WORK/escape.json" --script-delay 1.5
sleep 3
run "$LOOPOS" ps

step "3. Pause l3: its worker process gets SIGSTOP (state T)"
run "$LOOPOS" pause l3
sleep 2
run "$LOOPOS" ps
pid=$("$LOOPOS" show l3 | awk '/pid/ {gsub(/[()]/, "", $NF); print $NF; exit}')
[ -n "$pid" ] && [ "$pid" != "-" ] && run ps -o pid,stat,ni,cmd -p "$pid"
sleep 2
run "$LOOPOS" resume l3

step "4. Let them finish"
wait_all
run "$LOOPOS" ps -a

step "5. What happened in l1 (fixed its bug)"
run "$LOOPOS" logs l1
step "6. l2 kept repeating itself: livelock, rollback, then give up"
run "$LOOPOS" logs l2
step "7. l3 tried to read outside its workspace and run curl: refused, then fixed its bug"
run "$LOOPOS" logs l3
step "8. Scheduler decisions (who got the model, and why)"
"$LOOPOS" logs -n 400 | grep ' schedule ' | head -8

step "9. Benchmark: a loop per task, two at a time"
python3 -c "import json,sys; print(json.dumps([{'action':'write_file','path':'inventory.py','content':open(sys.argv[1]).read(),'reason':'restore'}]))" \
    "$HERE/inventory/inventory.py" > "$WORK/restore.json"
run "$LOOPOS" bench "$WORK/tasks" --parallel 2 --provider scripted --script "$WORK/restore.json" --script-delay 0.5

step "10. Training data from the traces"
run "$LOOPOS" export --out "$WORK/dataset.jsonl"
run "$LOOPOS" daemon stop
printf '\nState kept in %s (LOOPOS_HOME). Try: LOOPOS_HOME=%s %s ps -a\n' "$LOOPOS_HOME" "$LOOPOS_HOME" "$LOOPOS"

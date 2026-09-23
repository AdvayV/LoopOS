# LoopOS Crisis Lab

A fictional campus festival where incident-response plans compete for volunteers, one generator, one room, and a 300-credit budget. This is a coordination exercise, not advice for real emergencies.

## Run

Use the existing README backend and frontend setup. Start one FastAPI worker on localhost:8000 and Vite on localhost:5173. The homepage now shows Crisis Lab; original simulation API endpoints remain available.

1. Click **One step** to prepare the background supply plan.
2. Add **Power failure** and **Rain approaching**.
3. Click **Start exercise**. Urgent work is scheduled first; both incidents need the single generator, so rain waits if it is still reserved.
4. Select any incident to see its checked plan, stage, and visitor announcement.
5. Pause to inspect the trail. Open **OS learning tools** to see priority comparisons.
6. Use **Try an impossible plan** before a plan is reserved to see rejection. After a valid plan exists, this restores its checkpoint. Without a checkpoint, two identical invalid attempts stop the task.

## Codex without an API key

Install the Codex CLI, run `codex login`, and sign in with ChatGPT. In Crisis Lab, pause the exercise, click **Check Codex sign-in**, then **Use Codex**. This starts the local Codex App Server over stdio. No API-key field or API-key fallback is provided. Account access, internet and available Codex usage are still required.

Codex writes a structured fictional response proposal and announcement. It is not asked to read files or run commands; the thread requests read-only sandboxing and rejects approval requests. The deterministic rules checker independently validates integer resource quantities, required minima, cost and required plan fields. It cannot prove that prose is good emergency advice. Codex connection errors pause the run; selecting Demo is explicit. Each Codex turn has a 90-second response deadline, and three invalid proposals or two identical failures trigger the watchdog.

## OS behavior

- One scheduler slot; effective priority = base priority + floor(waiting cycles / 3).
- Longer waits break ties. Each completed stage is a safe switching boundary.
- Pause waits for an active Codex turn to finish. Reset/stop are rejected while it is active.
- All-or-nothing reservations prevent double booking and hold-and-wait deadlocks. This version prevents that class of deadlocks rather than constructing artificial deadlock cycles.
- Blocked incidents name their resource holders; runnable holders can finish and release resources.
- Communications depends on response completion. Its announcement is drafted with the logistics plan and published only afterward; these are workflow roles, not three independent language models.
- Checkpoints preserve valid plans. Credits are spent at reservation and not refunded when work is stopped; volunteers/equipment are released.
- SQLite retains ordered run events. Live exercise state is in memory; restart recovery of an unfinished world is not implemented.
- The web UI polls snapshots once per second; the original simulator retains its WebSocket endpoint.

## Architecture

`crisis.py` owns the world and invariants; `crisis_api.py` schedules bounded steps; `codex_bridge.py` connects Codex using managed ChatGPT authentication. React renders incident cards, resource availability, venue tiles and a plain-language trail. Demo plans and Codex plans go through the same checker.

Reference: https://learn.chatgpt.com/docs/app-server and https://learn.chatgpt.com/docs/auth

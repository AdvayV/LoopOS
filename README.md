# LoopOS

LoopOS is an operating-system-inspired control layer for iterative AI agents. It treats each agent loop as a schedulable process and demonstrates priority scheduling, aging, safe-boundary preemption, convergence detection, livelock protection, resource budgets, and checkpoint recovery.

The first prototype is deterministic: it requires no model API key and produces the same observable scheduling behavior on every reset.

## Demo scenarios

1. **Research Abstract Optimizer** improves over several iterations and terminates when its score converges.
2. **Repeated Draft Detector** enters a repeated-output livelock and is stopped by the watchdog, restoring its best checkpoint.
3. **Low-priority Literature Scan** waits behind more important work and can be safely preempted by an injected urgent loop. Priority aging prevents starvation.

## Run locally

Prerequisites: Python 3.11+ and Node.js 20+.

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs at `http://localhost:8000`; interactive documentation is available at `http://localhost:8000/docs`.

### Dashboard

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Test and build

```powershell
cd backend
python -m pytest

cd ..\frontend
npm run build
```

## Architecture

- `backend/app/simulation.py` — loop control blocks, scheduler, detectors, checkpoints, and deterministic agent provider.
- `backend/app/database.py` — SQLite run and event persistence.
- `backend/app/main.py` — FastAPI REST controls and WebSocket event stream.
- `frontend/src/App.jsx` — live React control-plane dashboard.

SQLite data is created at `backend/loopos.db` and excluded from version control. The provider boundary in the simulation engine can later be extended to call a local or cloud language model without changing scheduler semantics.

## Main API

- `GET /api/state` — current loop table, metrics, and recent events.
- `POST /api/control/start|pause|resume|reset` — scheduler lifecycle.
- `POST /api/control/inject-urgent` — add an urgent loop.
- `POST /api/loops/{loop_id}/stop` — stop a selected loop.
- `GET /api/runs` and `GET /api/runs/{id}/events` — persisted run history.
- `WS /ws` — ordered live kernel events.


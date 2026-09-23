from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .database import EventStore
from .simulation import LoopOSEngine
from .crisis_api import CrisisService, make_router

DATABASE_PATH = Path(os.getenv("LOOPOS_DB", Path(__file__).parents[1] / "loopos.db"))
store = EventStore(DATABASE_PATH)
engine = LoopOSEngine(store)
crisis = CrisisService(store)


@asynccontextmanager
async def lifespan(app):
    yield
    if crisis.worker and not crisis.worker.done():
        crisis.worker.cancel()
        try:
            await crisis.worker
        except asyncio.CancelledError:
            pass
    crisis.codex.close()

app = FastAPI(
    lifespan=lifespan,
    title="LoopOS API",
    version="0.1.0",
    description="An OS-inspired control layer for iterative AI agents.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(make_router(crisis))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def state() -> dict:
    return engine.snapshot()


@app.get("/api/runs")
def runs() -> list[dict]:
    return store.list_runs()


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: int) -> list[dict]:
    return store.list_events(run_id)


@app.post("/api/control/start")
async def start() -> dict:
    engine.start()
    return engine.snapshot()


@app.post("/api/control/pause")
async def pause() -> dict:
    engine.pause()
    return engine.snapshot()


@app.post("/api/control/resume")
async def resume() -> dict:
    engine.resume()
    return engine.snapshot()


@app.post("/api/control/reset")
async def reset() -> dict:
    engine.reset()
    return engine.snapshot()


@app.post("/api/control/inject-urgent")
async def inject_urgent() -> dict:
    engine.inject_urgent()
    return engine.snapshot()


@app.post("/api/loops/{loop_id}/stop")
async def stop_loop(loop_id: str) -> dict:
    if not engine.stop_loop(loop_id):
        raise HTTPException(status_code=409, detail="Loop cannot be stopped.")
    return engine.snapshot()


@app.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = engine.subscribe()
    await websocket.send_json({"type": "snapshot", "payload": engine.snapshot()})
    try:
        while True:
            event = await queue.get()
            await websocket.send_json({"type": "event", "payload": event})
    except WebSocketDisconnect:
        pass
    finally:
        engine.unsubscribe(queue)

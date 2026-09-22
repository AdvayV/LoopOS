from pathlib import Path

from app.database import EventStore
from app.simulation import LoopOSEngine, LoopState


def make_engine(tmp_path: Path) -> LoopOSEngine:
    return LoopOSEngine(EventStore(tmp_path / "test.db"))


def test_converging_loop_finishes_early(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    loop = engine.loops["converge"]
    while loop.state not in {LoopState.COMPLETED, LoopState.STOPPED}:
        engine._iterate(loop)
    assert loop.state == LoopState.COMPLETED
    assert loop.iterations < loop.max_iterations
    assert "Converged" in (loop.termination_reason or "")


def test_repeated_draft_is_stopped_and_recovers_checkpoint(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    loop = engine.loops["repeat"]
    while loop.state not in {LoopState.COMPLETED, LoopState.STOPPED}:
        engine._iterate(loop)
    assert loop.state == LoopState.STOPPED
    assert "livelocked" in (loop.termination_reason or "")
    assert loop.current_draft == loop.best_draft


def test_urgent_loop_preempts_lower_priority_work(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    background = engine.loops["background"]
    engine.last_dispatched_id = background.id
    background.state = LoopState.READY
    engine.loops["converge"].state = LoopState.COMPLETED
    engine.loops["repeat"].state = LoopState.COMPLETED
    urgent = engine.inject_urgent()
    engine.step()
    assert engine.last_dispatched_id == urgent.id
    assert engine.preemptions == 1
    assert background.state == LoopState.WAITING


def test_aging_raises_waiting_priority(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    background = engine.loops["background"]
    engine.loops["converge"].state = LoopState.COMPLETED
    engine.loops["repeat"].state = LoopState.COMPLETED
    background.wait_ticks = engine.AGING_INTERVAL * 2
    engine.step()
    assert background.history


def test_time_budget_stops_loop_and_keeps_checkpoint(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    loop = engine.loops["background"]
    loop.time_budget_seconds = 0.5
    engine._iterate(loop)
    assert loop.state == LoopState.STOPPED
    assert "Time budget" in (loop.termination_reason or "")
    assert loop.current_draft == loop.best_draft

import { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const WS = API.replace(/^http/, "ws");

const metricLabels = {
  total_loops: ["Total loops", "All processes in this run"],
  terminal_loops: ["Finished", "Completed or safely stopped"],
  preemptions: ["Preemptions", "Safe priority switches"],
  iterations_avoided: ["Iterations saved", "Work avoided by convergence"],
  repeated_drafts_detected: ["Livelocks caught", "Repeating agents stopped"],
  average_wait_ticks: ["Average wait", "Scheduler ticks spent waiting"],
};

const stateHelp = {
  ready: "Eligible for the next scheduler cycle",
  running: "Executing one safe iteration",
  paused: "Paused by the operator",
  waiting: "Yielded to higher-priority work",
  completed: "Finished successfully",
  stopped: "Stopped by a safety rule or operator",
  idle: "Ready to start",
};

function StatePill({ state }) {
  return <span className={`state state-${state}`}>{state}</span>;
}

function App() {
  const [data, setData] = useState(null);
  const [selectedId, setSelectedId] = useState("converge");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadState = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/state`);
      if (!response.ok) throw new Error("Backend returned an error");
      setData(await response.json());
      setError("");
    } catch {
      setError(`Cannot reach LoopOS at ${API}. Start the FastAPI server first.`);
    }
  }, []);

  useEffect(() => {
    loadState();
    let socket;
    let retry;
    const connect = () => {
      socket = new WebSocket(`${WS}/ws`);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        retry = window.setTimeout(connect, 1500);
      };
      socket.onmessage = (message) => {
        const packet = JSON.parse(message.data);
        if (packet.type === "snapshot") setData(packet.payload);
        else loadState();
      };
    };
    connect();
    return () => {
      window.clearTimeout(retry);
      if (socket) socket.close();
    };
  }, [loadState]);

  const command = async (action, path = `/api/control/${action}`) => {
    setBusy(action);
    try {
      const response = await fetch(`${API}${path}`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      setData(await response.json());
      setError("");
    } catch (requestError) {
      setError(requestError.message || "Command failed");
    } finally {
      setBusy("");
    }
  };

  const selected = useMemo(
    () => data?.loops.find((loop) => loop.id === selectedId) || data?.loops[0],
    [data, selectedId],
  );
  const loopNames = useMemo(
    () => Object.fromEntries((data?.loops || []).map((loop) => [loop.id, loop.name])),
    [data],
  );

  if (!data) {
    return <main className="loading"><div className="spinner" />{error || "Booting control plane..."}</main>;
  }

  const decision = data.last_decision;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><span /><span /><span /></div>
          <div><h1>Loop<span>OS</span></h1><p>AI process control plane</p></div>
        </div>
        <div className="system-status">
          <span className={`connection ${connected ? "online" : "offline"}`} />
          <span>{connected ? "Live updates connected" : "Reconnecting"}</span>
          <span className="run-id">Run {String(data.run_id).padStart(3, "0")}</span>
        </div>
      </header>

      <main className="content">
        <section className="hero">
          <div className="hero-copy">
            <span className="eyebrow">OPERATING-SYSTEM CONTROL FOR AI AGENTS</span>
            <h2>See every decision.<br /><em>Control every loop.</em></h2>
            <p>LoopOS treats each iterative AI task like an operating-system process. It schedules work, watches for wasted iterations, and preserves the best result.</p>
          </div>
          <div className="scheduler-card">
            <div className="scheduler-heading">
              <div><span className="section-label">SCHEDULER</span><strong>{stateHelp[data.scheduler_status] || "Simulation active"}</strong></div>
              <StatePill state={data.scheduler_status} />
            </div>
            <div className="controls">
              <button className="primary" disabled={busy || data.scheduler_status === "running"} onClick={() => command("start")}>Start simulation</button>
              <button disabled={busy || data.scheduler_status !== "running"} onClick={() => command("pause")}>Pause</button>
              <button disabled={busy || data.scheduler_status !== "paused"} onClick={() => command("resume")}>Resume</button>
              <button disabled={busy} onClick={() => command("reset")}>Reset</button>
              <button className="urgent" disabled={busy} onClick={() => command("inject-urgent")}>+ Add urgent loop</button>
            </div>
            <div className="policy"><span>Scheduling policy</span><strong>Priority + aging</strong><small>Switches happen only after a complete iteration.</small></div>
          </div>
        </section>

        {error && <div className="error-banner">{error}</div>}

        <section className="explanation" aria-label="How LoopOS works">
          <div className="explanation-intro"><span className="eyebrow">HOW IT WORKS</span><h3>One controlled cycle at a time</h3></div>
          {[
            ["1", "Queue", "AI loops enter the ready queue with a base priority."],
            ["2", "Choose", "The scheduler adds an aging bonus and selects the highest score."],
            ["3", "Improve", "The chosen loop produces and evaluates one new draft."],
            ["4", "Protect", "Safety rules converge, stop, or recover the loop when needed."],
          ].map(([number, title, text]) => <article className="explain-step" key={number}><b>{number}</b><div><strong>{title}</strong><p>{text}</p></div></article>)}
        </section>

        <section className="metrics">
          {Object.entries(data.metrics).map(([key, value]) => (
            <article className="metric" key={key}>
              <span>{metricLabels[key]?.[0] || key}</span>
              <strong>{value}</strong>
              <small>{metricLabels[key]?.[1]}</small>
            </article>
          ))}
        </section>

        <section className="decision-panel panel">
          <div className="panel-title">
            <div><span className="kicker">SCHEDULER REASONING</span><h3>{decision ? `Decision cycle ${decision.cycle}` : "Waiting for the first decision"}</h3></div>
            <span className="formula">{data.policy.formula}</span>
          </div>
          {decision ? <div className="decision-content">
            <div className="decision-summary"><span>WHY THIS LOOP?</span><p>{decision.message}</p></div>
            <div className="candidate-list">
              {decision.candidates.map((candidate, index) => (
                <div className={`candidate ${index === 0 ? "winner" : ""}`} key={candidate.id}>
                  <span className="rank">{index + 1}</span>
                  <div><strong>{candidate.name}</strong><small>Base {candidate.base_priority} + aging {candidate.aging_bonus}</small></div>
                  <b>{candidate.effective_priority}</b>
                </div>
              ))}
            </div>
          </div> : <div className="empty-decision"><strong>Press Start simulation.</strong><span>LoopOS will show every candidate and explain why the winner was selected.</span></div>}
        </section>

        <section className="workspace">
          <div className="panel loop-panel">
            <div className="panel-title"><div><span className="kicker">LOOP TABLE</span><h3>AI process queue</h3></div><span className="count">{data.loops.length} loops</span></div>
            <p className="panel-help">Select a row to inspect its draft, safeguards, and iteration history.</p>
            <div className="table-wrap">
              <table>
                <thead><tr><th>AI loop</th><th>State</th><th>Effective priority</th><th>Progress</th><th>Quality</th><th>Token budget</th></tr></thead>
                <tbody>{data.loops.map((loop) => (
                  <tr key={loop.id} className={selected?.id === loop.id ? "selected" : ""} onClick={() => setSelectedId(loop.id)}>
                    <td><strong>{loop.name}</strong><small>{loop.task}</small></td>
                    <td><StatePill state={loop.state} /><small>{stateHelp[loop.state]}</small></td>
                    <td><b className="priority-number">{loop.effective_priority}</b><small>Base {loop.base_priority}, waited {loop.wait_ticks}</small></td>
                    <td><strong>{loop.iterations} / {loop.max_iterations}</strong><small>iterations</small></td>
                    <td><span className="score">{Math.round(loop.score * 100)}%</span><small>best {Math.round(loop.best_score * 100)}%</small></td>
                    <td><div className="budget"><span style={{ width: `${Math.min(loop.tokens_used / loop.token_budget * 100, 100)}%` }} /></div><small>{loop.tokens_used} / {loop.token_budget}</small></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>

          <aside className="panel inspector">
            <div className="panel-title"><div><span className="kicker">SELECTED LOOP</span><h3>Loop details</h3></div></div>
            {selected && <>
              <div className="inspector-head"><div className="loop-glyph">LOOP</div><div><h4>{selected.name}</h4><p>{selected.task}</p></div></div>
              <dl>
                <div><dt>Current state</dt><dd><StatePill state={selected.state} /></dd></div>
                <div><dt>Effective priority</dt><dd>{selected.effective_priority} <small>(base {selected.base_priority})</small></dd></div>
                <div><dt>Best checkpoint</dt><dd>{Math.round(selected.best_score * 100)}%</dd></div>
                <div><dt>Resources used</dt><dd>{selected.tokens_used} tokens / {selected.elapsed_seconds}s</dd></div>
                <div><dt>Safety counters</dt><dd>repeat {selected.repeated_drafts}/{data.policy.repeat_limit}, stagnant {selected.stagnant_steps}/{data.policy.watchdog_limit}</dd></div>
              </dl>
              <div className="draft"><span>LATEST DRAFT</span><p>{selected.current_draft}</p></div>
              {selected.history.length > 0 && <div className="history"><span>QUALITY TRAIL</span>{selected.history.slice(-5).reverse().map((item) => <div key={item.iteration}><b>Iteration {item.iteration}</b><span>{Math.round(item.score * 100)}%</span></div>)}</div>}
              {selected.termination_reason && <div className="reason"><span>FINAL OUTCOME</span>{selected.termination_reason}</div>}
              {!['completed', 'stopped'].includes(selected.state) && <button className="stop" disabled={busy} onClick={() => command("stop", `/api/loops/${selected.id}/stop`)}>Stop this loop safely</button>}
            </>}
          </aside>
        </section>

        <section className="panel event-panel">
          <div className="panel-title"><div><span className="kicker">EXPLAINABLE EVENT TRAIL</span><h3>What happened, and why</h3></div><span className="live-dot"><i />Live</span></div>
          <div className="trail-header"><span>Time</span><span>Event</span><span>Loop</span><span>Explanation</span></div>
          <div className="events">
            {data.events.map((event) => (
              <div className="event" key={`${event.sequence}-${event.timestamp}`}>
                <time>{new Date(event.timestamp).toLocaleTimeString([], { hour12: false })}</time>
                <span className={`event-type type-${event.type}`}>{event.type}</span>
                <strong>{event.loop_id ? loopNames[event.loop_id] || event.loop_id : "LoopOS"}</strong>
                <p>{event.message}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer><span>LoopOS prototype v0.2</span><span>Deterministic simulation | SQLite history | WebSocket updates</span></footer>
    </div>
  );
}

export default App;

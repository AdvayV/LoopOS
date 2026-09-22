import { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const WS = API.replace(/^http/, "ws");

const labels = {
  total_loops: "Loops",
  terminal_loops: "Terminal",
  preemptions: "Preemptions",
  iterations_avoided: "Iterations saved",
  repeated_drafts_detected: "Livelocks caught",
  average_wait_ticks: "Avg. wait ticks",
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
    } catch (requestError) {
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

  if (!data) {
    return <main className="loading"><div className="spinner" />{error || "Booting control plane…"}</main>;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><span /><span /><span /></div>
          <div>
            <h1>Loop<span>OS</span></h1>
            <p>Agent process control plane</p>
          </div>
        </div>
        <div className="system-status">
          <span className={`connection ${connected ? "online" : "offline"}`} />
          {connected ? "Live event stream" : "Reconnecting"}
          <span className="run-id">RUN {String(data.run_id).padStart(3, "0")}</span>
        </div>
      </header>

      <main className="content">
        <section className="hero">
          <div>
            <div className="eyebrow">SIMULATION CONTROL</div>
            <h2>Schedule intelligence.<br /><em>Control iteration.</em></h2>
            <p>OS-inspired scheduling, safe preemption, convergence detection, and recovery for autonomous AI loops.</p>
          </div>
          <div className="scheduler-card">
            <div className="scheduler-heading">
              <span>Scheduler</span>
              <StatePill state={data.scheduler_status} />
            </div>
            <div className="controls">
              <button className="primary" disabled={busy || data.scheduler_status === "running"} onClick={() => command("start")}>▶ Start</button>
              <button disabled={busy || data.scheduler_status !== "running"} onClick={() => command("pause")}>Ⅱ Pause</button>
              <button disabled={busy || data.scheduler_status !== "paused"} onClick={() => command("resume")}>↻ Resume</button>
              <button disabled={busy} onClick={() => command("reset")}>Reset</button>
              <button className="urgent" disabled={busy} onClick={() => command("inject-urgent")}>+ Inject urgent</button>
            </div>
            <p className="policy">POLICY <strong>Priority + aging</strong><span>Safe-boundary preemption</span></p>
          </div>
        </section>

        {error && <div className="error-banner">{error}</div>}

        <section className="metrics">
          {Object.entries(data.metrics).map(([key, value]) => (
            <article className="metric" key={key}>
              <span>{labels[key]}</span>
              <strong>{value}</strong>
              <i />
            </article>
          ))}
        </section>

        <section className="workspace">
          <div className="panel loop-panel">
            <div className="panel-title">
              <div><span className="kicker">LOOP TABLE</span><h3>Process queue</h3></div>
              <span className="count">{data.loops.length} LCBs</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Loop</th><th>State</th><th>Priority</th><th>Iteration</th><th>Score</th><th>Budget</th></tr></thead>
                <tbody>
                  {data.loops.map((loop) => (
                    <tr key={loop.id} className={selected?.id === loop.id ? "selected" : ""} onClick={() => setSelectedId(loop.id)}>
                      <td><strong>{loop.name}</strong><small>{loop.id}</small></td>
                      <td><StatePill state={loop.state} /></td>
                      <td><b>{loop.effective_priority}</b><small>base {loop.base_priority}</small></td>
                      <td>{loop.iterations}<small>of {loop.max_iterations}</small></td>
                      <td><span className="score">{Math.round(loop.score * 100)}%</span></td>
                      <td><div className="budget"><span style={{ width: `${Math.min(loop.tokens_used / loop.token_budget * 100, 100)}%` }} /></div><small>{loop.tokens_used} / {loop.token_budget}</small></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <aside className="panel inspector">
            <div className="panel-title"><div><span className="kicker">LOOP CONTROL BLOCK</span><h3>Inspector</h3></div></div>
            {selected && <>
              <div className="inspector-head"><div className="loop-glyph">∞</div><div><h4>{selected.name}</h4><p>{selected.task}</p></div></div>
              <dl>
                <div><dt>Current state</dt><dd><StatePill state={selected.state} /></dd></div>
                <div><dt>Wait / age</dt><dd>{selected.wait_ticks} ticks</dd></div>
                <div><dt>Best checkpoint</dt><dd>{Math.round(selected.best_score * 100)}%</dd></div>
                <div><dt>Repeat count</dt><dd>{selected.repeated_drafts}</dd></div>
              </dl>
              <div className="draft"><span>LATEST DRAFT</span><p>{selected.current_draft}</p></div>
              {selected.termination_reason && <div className="reason"><span>TERMINATION</span>{selected.termination_reason}</div>}
              {!['completed', 'stopped'].includes(selected.state) && <button className="stop" disabled={busy} onClick={() => command("stop", `/api/loops/${selected.id}/stop`)}>Stop selected loop</button>}
            </>}
          </aside>
        </section>

        <section className="panel event-panel">
          <div className="panel-title"><div><span className="kicker">KERNEL EVENTS</span><h3>Live event log</h3></div><span className="live-dot">● LIVE</span></div>
          <div className="events">
            {data.events.map((event) => (
              <div className="event" key={`${event.sequence}-${event.timestamp}`}>
                <time>{new Date(event.timestamp).toLocaleTimeString([], { hour12: false })}</time>
                <span className={`event-type type-${event.type}`}>{event.type}</span>
                <code>#{String(event.sequence).padStart(3, "0")}</code>
                <p>{event.message}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer><span>LoopOS prototype v0.1</span><span>Deterministic simulation · SQLite persistence · WebSocket telemetry</span></footer>
    </div>
  );
}

export default App;


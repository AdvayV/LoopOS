import { useEffect, useState } from 'react';
import { createBrowserDemo } from './browserDemo';

const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : '');
const browserOnly = !API;
const STAGES = {plan: 'Logistics: prepare a plan', reserve: 'Checker: reserve resources', respond: 'Response team: carry out plan', announce: 'Communications: publish update'};
const INCIDENTS = [['power', 'Power failure', 'Main stage'], ['rain', 'Rain approaching', 'Indoor hall'], ['crowd', 'Growing queue', 'Entrance']];
const LABELS = {queued: 'Ready', blocked: 'Waiting for resources', responding: 'Responding', resolved: 'Resolved', stopped: 'Stopped'};
const localDemo = browserOnly ? createBrowserDemo() : null;

export default function App() {
  const [world, setWorld] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer;
    async function refresh() {
      try {
        if (browserOnly) { const data = await localDemo.get(); if (!cancelled) setWorld(data); }
        else {
        const response = await fetch(`${API}/api/crisis/state`);
        if (!response.ok) throw new Error('Server unavailable');
        const data = await response.json();
        if (!cancelled) setWorld(data);
        }
      } catch { if (!cancelled) setError('Cannot reach the server. Start the backend, then reload this page.'); }
      if (!cancelled) timer = setTimeout(refresh, 1000);
    }
    refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);
  async function command(path, value) {
    setPending(true); setError('');
    try {
      if (browserOnly) { setWorld(await localDemo.post(path,value)); return; }
      const response = await fetch(`${API}/api/crisis/${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, ...(value ? {body: JSON.stringify({value})} : {})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Action failed');
      setWorld(result);
    } catch (e) { setError(e.message); }
    finally { setPending(false); }
  }
  if (!world) return <main className="boot"><h1>LoopOS Crisis Lab</h1><p>{error || 'Preparing the festival…'}</p><button onClick={() => location.reload()}>Reload</button></main>;
  const item = world.incidents.find(i => i.id === selected) || world.incidents.find(i => i.status !== 'resolved') || world.incidents[0];
  const active = world.incidents.filter(i => !['resolved', 'stopped'].includes(i.status));
  const running = world.status === 'running';
  const disabled = pending || world.busy;
  return <>
    <header><a className="brand" href="#"><span className="logo">L</span><span>LoopOS <b>Crisis Lab</b></span></a><span className="simulation-tag">Fictional festival exercise</span></header>
    <main>
      <section className="hero"><div><span className="eyebrow">YOUR FESTIVAL. YOUR CONTROL ROOM.</span><h1>Keep the festival<br/><span>moving.</span></h1><p>Handle surprises, share limited resources, and watch your response team make a plan. Every decision has a visible reason.</p></div>
        <div className="welcome"><span className="step-label">START HERE</span><h2>Three simple steps</h2><ol><li>Start the festival exercise.</li><li>Add a power failure, rain, or a growing queue.</li><li>Watch plans get checked and resources move.</li></ol><p className="muted">Demo is repeatable. Codex mode creates real AI plans through your local ChatGPT sign-in.</p></div>
      </section>
      <section className="toolbar"><div className="status"><i className={running ? 'on' : ''}/><strong>{world.busy ? 'Finishing an agent turn…' : running ? 'Festival running' : world.status === 'completed' ? 'All tasks finished' : 'Ready when you are'}</strong><small>Cycle {world.tick}</small></div><div className="buttons">
        <button className="primary" disabled={pending || running || world.busy} onClick={() => command('control/start')}>{world.tick ? 'Continue' : 'Start exercise'}</button>
        <button disabled={pending || !running} onClick={() => command('control/pause')}>Pause</button>
        <button disabled={disabled || running} onClick={() => command('control/step')}>One step</button>
        <button disabled={disabled} onClick={() => {if (confirm('Start a fresh exercise? This run stays in saved event history.')) command('control/reset');}}>Reset</button>
      </div></section>
      {error && <div role="alert" className="error">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
      <section className="resources" aria-label="Available resources">
        {Object.entries(world.resources).map(([name, available]) => <article key={name}><span>{name}</span><strong>{available}<small> / {world.capacity[name]}</small></strong><p>available to assign</p><progress max={world.capacity[name]} value={available}/></article>)}
        <article><span>Festival budget</span><strong>{world.budget}<small> credits</small></strong><p>Spent when a response starts</p></article>
        <article className="success"><span>Problems resolved</span><strong>{world.metrics.resolved}<small> / {world.incidents.length}</small></strong><p>{active.length} tasks still in progress</p></article>
      </section>
      <div className="workspace"><section className="left-column">
        <section className="panel"><div className="heading"><div><span className="eyebrow">FESTIVAL GROUNDS</span><h2>What’s happening where?</h2></div><span className="pill">Campus festival</span></div><div className="venue-map">
          {['Main stage', 'Indoor hall', 'Food court', 'Entrance'].map((zone, index) => {const incidents = active.filter(i => i.zone === zone); return <button className={`zone zone-${index} ${incidents.length ? 'trouble' : ''}`} key={zone} onClick={() => {const found = world.incidents.find(i => i.zone === zone); if (found) setSelected(found.id);}}><span className="zone-icon">{['♫', '⌂', '☕', '↗'][index]}</span><strong>{zone}</strong><small>{incidents.length ? incidents[0].title : 'No active incident'}</small><span className="zone-status">{incidents.length ? 'Needs attention' : 'All clear'}</span></button>;})}
        </div><div className="inject"><strong>Introduce a surprise</strong><div className="buttons">{INCIDENTS.map(([kind, title]) => <button key={kind} disabled={pending || active.some(i => i.kind === kind)} onClick={() => command('incidents', kind)}>+ {title}</button>)}</div></div></section>
        <section className="panel"><div className="heading"><div><span className="eyebrow">RESPONSE QUEUE</span><h2>Every task, one place</h2></div></div><div className="task-list">{world.incidents.map(i => <button className={`task ${item.id === i.id ? 'selected' : ''}`} key={i.id} onClick={() => setSelected(i.id)}><div><span className={`badge ${i.status}`}>{LABELS[i.status]}</span><strong>{i.title}</strong><small>{['resolved','stopped'].includes(i.status) ? i.status === 'resolved' ? 'Resources released. Notice published.' : i.error : STAGES[i.stage]}</small></div><span className="priority">P{i.priority}<small>priority</small></span></button>)}</div></section>
      </section>
      <aside className="panel detail"><div className="heading"><div><span className="eyebrow">SELECTED INCIDENT</span><h2>{item.title}</h2></div></div><div className="detail-body"><p>{item.description}</p><div className="stage-list">{Object.entries(STAGES).map(([key, text], index) => <div className={item.stage === key ? 'current' : ''} key={key}><b>{index+1}</b><span>{text}</span></div>)}</div>
        <h3>What it needs</h3><div className="chips">{Object.entries(item.needs).map(([k,v]) => <span key={k}>{v} {k}</span>)}<span>{item.cost} credits minimum</span></div>
        {item.blocked_by.length > 0 && <p className="notice">Waiting for: {item.blocked_by.map(id => world.incidents.find(i => i.id === id)?.title).join(', ')}. The resource holder can continue working.</p>}
        {item.plan ? <><h3>Checked response plan</h3><p>{item.plan.summary}</p><ol className="plan-steps">{item.plan.steps.map((s,n) => <li key={n}>{s}</li>)}</ol><p className="muted">Checkpoint saved. {Object.keys(item.allocation).length ? 'Resources reserved.' : 'Resources are assigned only when available.'}</p></> : <p className="empty">A response plan will appear when this task gets a turn.</p>}
        {item.announcement && <blockquote><strong>Visitor notice</strong><p>{item.announcement}</p></blockquote>}
        {item.error && <p className="notice">{item.error}</p>}
        {item.history.length > 0 && <details><summary>Plan attempts ({item.history.length})</summary>{item.history.map((h,n) => <p key={n}><b>{h.source} · {h.accepted ? 'Passed' : 'Rejected'}</b><br/>{h.reason}</p>)}</details>}
        {!['resolved','stopped'].includes(item.status) && <button className="danger" disabled={disabled} onClick={() => command(`incidents/${item.id}/stop`)}>Stop task & release resources</button>}
      </div></aside></div>
      <section className="panel trail"><div className="heading"><div><span className="eyebrow">DECISION TRAIL</span><h2>What happened, and why</h2></div><span className="muted">Newest first</span></div><div className="events">{world.events.map(e => <article key={`${world.run_id}-${e.sequence}`}><time>{new Date(e.timestamp).toLocaleTimeString()}</time><span className={`event-type ${e.type}`}>{e.type}</span><p>{e.message}</p></article>)}</div></section>
      <section className="panel settings"><div><span className="eyebrow">PLANNING ENGINE</span><h2>{browserOnly ? 'Browser demo' : world.mode === 'demo' ? 'Demo planner' : 'Codex planner'}</h2><p>{browserOnly ? 'Runs in this browser. No server, sign-in, or API key needed.' : world.mode === 'demo' ? 'Scripted plans, predictable results, no connection needed.' : 'Real AI proposals. The festival rules still decide what is allowed.'}</p><p className="muted">{browserOnly ? 'Codex mode is available when running the full app locally.' : world.connection.message}</p></div>{!browserOnly && <div className="buttons"><button disabled={disabled || running} onClick={() => command('codex/check')}>Check Codex sign-in</button><button disabled={disabled || running} onClick={() => command('mode', world.mode === 'demo' ? 'codex' : 'demo')}>Use {world.mode === 'demo' ? 'Codex' : 'Demo'}</button></div>}</section>
      <button className="text-button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>{advanced ? 'Hide' : 'Show'} OS learning tools</button>
      {advanced && <section className="panel learning"><h2>How the operating-system ideas work</h2><p>Higher priorities go first. Every three waiting cycles adds one priority point. A task switches only between finished steps. Resources are reserved together, preventing partial-allocation deadlocks. Visitor announcements depend on the response finishing.</p><div className="chips"><span>{world.metrics.switches} task switches</span><span>{world.metrics.rejected} invalid plans rejected</span><span>{world.metrics.recovered} checkpoints restored</span></div>{world.decision && <div className="rankings"><h3>Last scheduling decision</h3>{world.decision.candidates.map((c,n) => <p key={n}>{c.title}: {c.base} base + {c.age_bonus} waiting bonus = <b>{c.score}</b></p>)}</div>}<button disabled={disabled} onClick={() => command('control/invalid-plan')}>Try an impossible plan</button><p className="muted">Requests two generators when only one exists. Watch rejection, checkpoint recovery, or the retry watchdog without changing resources.</p></section>}
      <footer>LoopOS Crisis Lab · Fictional coordination exercise, not emergency advice · Run {world.run_id}</footer>
    </main>
  </>;
}

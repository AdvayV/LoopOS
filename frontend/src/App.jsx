import { useEffect, useState } from 'react';
import { createBrowserDemo } from './browserDemo';
import { STAGES, TERMINAL, eventLabel, resourceLabel, taskState, waitingReason, schedulerChoice, simulationState } from './uiModel';

const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : '');
const browserOnly = !API;
const localDemo = browserOnly ? createBrowserDemo() : null;
const SCENARIOS = [
  {kind: 'power', title: 'Power failure', zone: 'Main stage', icon: 'bolt', note: 'An urgent task that needs the generator.'},
  {kind: 'rain', title: 'Rain approaching', zone: 'Indoor hall', icon: 'rain', note: 'Another task competing for the same generator.'},
  {kind: 'crowd', title: 'Growing entrance queue', zone: 'Entrance', icon: 'people', note: 'A response that needs four volunteers.'},
];
const ICONS = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  list: <><path d="M9 6h12M9 12h12M9 18h12"/><path d="M3 6h1M3 12h1M3 18h1"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  book: <><path d="M12 5v15M12 5C9 3 6 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z"/></>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
  play: <path d="m8 5 11 7-11 7Z"/>,
  pause: <><path d="M8 5v14M16 5v14"/></>,
  step: <><path d="m5 5 10 7-10 7ZM19 5v14"/></>,
  reset: <><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/></>,
  bolt: <path d="m13 2-9 12h7l-1 8 10-13h-7Z"/>,
  people: <><circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 4v2"/></>,
  room: <><path d="M4 21V3h16v18M2 21h20M8 21V7h8v14M13 14h.1"/></>,
  rain: <><path d="M6 15a4 4 0 0 1-1-8 6 6 0 0 1 11-1 4.5 4.5 0 0 1 2 9M8 18l-1 3M13 18l-1 3M18 18l-1 3"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
  settings: <><path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
};

function Icon({name, size = 20}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICONS[name] || ICONS.info}</svg>;
}

function SectionTitle({number, title, subtitle, children}) {
  return <div className="section-heading"><div className="section-title"><span className="section-number">{number}</span><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div></div>{children}</div>;
}

export function Dashboard({world, selected, onSelect, onCommand, pending = false, error = '', onDismissError, browserMode = false}) {
  const [guideOpen, setGuideOpen] = useState(false);
  const [taskFilter, setTaskFilter] = useState('all');
  const [trailFilter, setTrailFilter] = useState('all');
  const active = world.incidents.filter(i => !TERMINAL.includes(i.status));
  const item = world.incidents.find(i => i.id === selected) || active[0] || world.incidents[0];
  const running = world.status === 'running';
  const disabled = pending || world.busy;
  const state = simulationState(world);
  const choice = schedulerChoice(world);
  const task = item ? taskState(item) : null;
  const shownTasks = world.incidents.filter(i => taskFilter === 'all' || (taskFilter === 'open' ? !TERMINAL.includes(i.status) : TERMINAL.includes(i.status)));
  const events = world.events.filter(e => trailFilter === 'all' || e.loop_id === item?.id);
  const blocked = active.filter(i => i.status === 'blocked');
  const planner = browserMode ? 'Browser demo' : world.mode === 'codex' ? 'Codex AI' : 'Demo planner';
  const startLabel = running ? 'Pause simulation' : world.tick ? 'Resume simulation' : 'Start simulation';

  function reset() {
    const message = browserMode ? 'Start a fresh simulation? This browser run will be cleared.' : 'Start a fresh simulation? This run remains in the saved event history.';
    if (window.confirm(message)) onCommand('control/reset');
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="LoopOS Crisis Lab home"><span className="logo">L<span/></span><span>LoopOS<small>CRISIS LAB</small></span></a>
      <p className="nav-label">YOUR CONTROL ROOM</p>
      <nav aria-label="Dashboard sections">
        <a href="#overview"><Icon name="grid"/>Overview</a>
        <a href="#tasks"><Icon name="list"/>Response tasks<span className="nav-count">{active.length}</span></a>
        <a href="#activity"><Icon name="clock"/>Decision timeline</a>
        <a href="#learning"><Icon name="book"/>How LoopOS works</a>
      </nav>
      <div className="sidebar-note"><span className="live-dot"/>A safe place to experiment<p>Fictional festival, real scheduling rules. No actions affect a real event.</p></div>
      <div className="sidebar-footer">A little operating system<br/>for competing AI tasks.</div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb">LoopOS <span>/</span> Festival simulation</div><div className="topbar-actions"><span className="mode-tag"><span className="live-dot"/>{planner}</span><button className="quiet-button" onClick={() => setGuideOpen(!guideOpen)} aria-expanded={guideOpen} aria-controls="quick-guide"><Icon name="info"/>Quick guide</button></div></header>
      <main id="main-content">
        <section id="overview" className="page-intro"><div><p className="eyebrow">LESS GUESSWORK. MORE VISIBILITY.</p><h1>Festival control room<span>.</span></h1><p>See which task gets a turn, what it needs, and why another task has to wait.</p></div><span className="exercise-label"><Icon name="room"/>Campus festival exercise</span></section>
        {guideOpen && <section id="quick-guide" className="quick-guide" aria-label="Quick guide"><div><span>01</span><h3>Add a situation</h3><p>Try a power failure and approaching rain. Both need the single generator.</p></div><div><span>02</span><h3>Start or take one step</h3><p>Watch the scheduler choose a task. Pause whenever you want to inspect it.</p></div><div><span>03</span><h3>Follow the explanation</h3><p>Open a task to see its checked plan, resource holders, and progress.</p></div></section>}
        {error && <div role="alert" className="error-banner"><Icon name="info"/><p>{error}</p>{onDismissError && <button onClick={onDismissError} aria-label="Dismiss error">Dismiss</button>}</div>}
        <section className="simulation-controls" aria-label="Simulation controls"><div className="run-state"><span className={`state-dot ${world.status}`}/><div><strong>{state.title}</strong><p>{state.description}</p></div></div><div className="controls"><button className="primary" disabled={pending || (!running && (world.busy || !active.length))} onClick={() => onCommand(running ? 'control/pause' : 'control/start')}><Icon name={running ? 'pause' : 'play'}/>{startLabel}</button><button disabled={disabled || running || !active.length} onClick={() => onCommand('control/step')} title={running ? 'Pause the simulation to advance one step at a time.' : 'Run one scheduler turn, then inspect the result.'}><Icon name="step"/>One step</button><button className="reset-button" disabled={disabled} onClick={reset}><Icon name="reset"/>Reset</button></div></section>
        <div className="overview-grid"><section className="scheduler-card"><div className="card-eyebrow"><Icon name="arrow"/>LAST SCHEDULER CHOICE<span>Turn {choice ? world.decision.cycle : 0}</span></div><h2>{choice ? choice.title : 'Your first task is ready'}</h2><p>{choice ? choice.reason : 'Start the simulation or take one step to prepare the supply task. Add an urgent situation to see priorities in action.'}</p><div className="scheduler-bottom">{choice ? <span className="score-chip">Priority {choice.base} + waiting bonus {choice.age_bonus} = <b>{choice.score}</b></span> : <span className="score-chip">No resources reserved yet</span>}<span className="turn-label">{world.tick} scheduler turns</span></div></section>
          <section className="summary-card" aria-label="Response summary"><div className="summary-stat"><span className="stat-icon"><Icon name="list"/></span><div><strong>{active.length}</strong><span>open {active.length === 1 ? 'task' : 'tasks'}</span></div></div><div className="summary-stat"><span className="stat-icon success"><Icon name="check"/></span><div><strong>{world.metrics.resolved}</strong><span>resolved</span></div></div><div className="summary-foot"><span className={blocked.length ? 'waiting-dot' : 'live-dot'}/>{blocked.length ? `${blocked.length} ${blocked.length === 1 ? 'task is' : 'tasks are'} waiting for resources` : 'No tasks waiting for resources'}</div></section></div>
        <section className="resource-section" aria-labelledby="resource-heading"><div className="compact-heading"><h2 id="resource-heading">What is available?</h2><span>Resources are shared across every task.</span></div><div className="resource-grid">{Object.entries(world.resources).map(([name, available]) => {
          const holders = world.incidents.filter(i => (i.allocation[name] || 0) > 0);
          return <article className="resource-card" key={name}><div className="resource-top"><span className="resource-icon"><Icon name={name === 'volunteers' ? 'people' : name === 'generators' ? 'bolt' : 'room'}/></span><h3>{resourceLabel(name)}</h3><span className={`availability ${available ? '' : 'occupied'}`}>{available ? 'Available' : 'In use'}</span></div><div className="resource-value">{available}<span>of {world.capacity[name]} free</span></div><progress aria-label={`${resourceLabel(name)} available`} max={world.capacity[name]} value={available}/><p>{holders.length ? `In use by ${holders.map(i => i.title).join(' and ')}.` : 'Ready to assign to a response.'}</p></article>;
        })}<article className="resource-card budget-card"><div className="resource-top"><span className="resource-icon">C</span><h3>Budget</h3></div><div className="resource-value">{world.budget}<span>credits left</span></div><p>Charged when resources are reserved. Stopping a task does not refund credits.</p></article></div></section>
        <section className="scenario-section" aria-labelledby="scenario-heading"><SectionTitle number="1" title="Add a situation" subtitle="Introduce competing work and see how LoopOS responds."/><div className="scenario-grid">{SCENARIOS.map(s => {
          const exists = active.some(i => i.kind === s.kind);
          return <article className="scenario-card" key={s.kind}><span className={`scenario-icon ${s.kind}`}><Icon name={s.icon} size={23}/></span><div><span className="scenario-location">{s.zone}</span><h3>{s.title}</h3><p>{s.note}</p></div><button disabled={pending || exists} onClick={() => onCommand('incidents', s.kind)} aria-label={exists ? `${s.title} already added` : `Add ${s.title}`}>{exists ? <Icon name="check"/> : <Icon name="plus"/>}{exists ? 'Added' : 'Add'}</button></article>;
        })}</div></section>
        <section id="tasks" className="task-section"><SectionTitle number="2" title="Follow the response" subtitle="Select a task to understand its progress and next action."/><div className="task-workspace"><section className="panel queue-panel" aria-label="Response task queue"><div className="panel-heading"><h3>Response tasks <span className="count-badge">{world.incidents.length}</span></h3></div><div className="segmented-control" role="group" aria-label="Filter response tasks">{[['all', 'All'], ['open', 'Open'], ['finished', 'Finished']].map(([key, label]) => <button key={key} aria-pressed={taskFilter === key} onClick={() => setTaskFilter(key)}>{label}</button>)}</div><div className="task-list">{shownTasks.length ? shownTasks.map(i => {
          const view = taskState(i);
          return <button className={`task-card ${item?.id === i.id ? 'selected' : ''}`} key={i.id} onClick={() => onSelect(i.id)} aria-pressed={item?.id === i.id}><div className="task-card-top"><span className={`badge ${i.status}`}>{view.label}</span><span className={`urgency ${i.priority >= 8 ? 'urgent' : ''}`}>{i.priority >= 8 ? 'Urgent' : i.priority >= 5 ? 'Important' : 'Routine'}</span></div><strong>{i.title}</strong><span className="task-location">{i.zone}</span><p>{view.next}</p><div className="task-mini-progress" aria-hidden="true">{view.steps.map((s, n) => <span key={n} className={s}/>)}</div><div className="task-card-bottom"><span>Base priority {i.priority}</span><span>{item?.id === i.id ? 'Viewing details' : 'View details'} <Icon name="arrow" size={14}/></span></div></button>;
        }) : <p className="empty-state">No {taskFilter === 'finished' ? 'finished' : 'open'} tasks yet. {taskFilter === 'finished' ? 'Completed and stopped tasks will appear here.' : 'Add a situation to create more work.'}</p>}</div><p className="queue-note"><Icon name="info"/>Urgency is the base priority. Waiting time can increase a task's scheduling score.</p></section>
          {item ? <aside className="panel detail-panel" aria-label="Selected task details"><div className="detail-heading"><div><span className="eyebrow">SELECTED TASK · {item.zone}</span><h2>{item.title}</h2></div><span className={`badge ${item.status}`}>{task.label}</span></div><div className="detail-body"><p className="task-description">{item.description}</p><div className={`next-action ${item.status === 'blocked' ? 'waiting' : ''}`}><Icon name={item.status === 'resolved' ? 'check' : item.status === 'blocked' ? 'clock' : 'arrow'}/><div><strong>{task.heading}</strong><p>{item.status === 'blocked' ? waitingReason(item, world) : task.next}</p></div></div>
            <ol className="stage-track" aria-label="Response progress">{STAGES.map((stage, n) => <li key={stage.key} className={task.steps[n]} aria-current={task.steps[n] === 'current' ? 'step' : undefined}><span>{task.steps[n] === 'done' ? <Icon name="check" size={15}/> : n + 1}</span><div><strong>{stage.label}</strong><small>{task.steps[n] === 'done' ? 'Complete' : task.steps[n] === 'current' ? 'Next / in progress' : task.steps[n] === 'stopped' ? 'Stopped here' : 'Not started'}</small></div></li>)}</ol>
            <div className="detail-section"><h3>Resources required</h3><div className="chips">{Object.entries(item.needs).filter(([, v]) => v > 0).map(([k, v]) => <span key={k}>{v} {resourceLabel(k, v)}</span>)}<span>{item.cost} credits minimum</span></div><p className="helper-text">{Object.values(item.allocation).some(v => v > 0) ? 'Reserved for this task. Other tasks cannot use these resources yet.' : item.status === 'resolved' ? 'All reserved resources have been released.' : item.status === 'stopped' ? 'Any reserved resources have been released. Spent credits stay spent.' : 'Nothing is reserved until the whole request can be met.'}</p></div>
            <div className="detail-section"><div className="detail-section-heading"><h3>Response plan</h3>{item.plan && <span className="verified"><Icon name="check" size={14}/>Rules checked</span>}</div>{item.plan ? <><p>{item.plan.summary}</p><ol className="plan-steps">{item.plan.steps.map((s, n) => <li key={n}>{s}</li>)}</ol><p className="checkpoint-note"><Icon name="check" size={16}/>A valid plan is saved as a recovery checkpoint.</p></> : <div className="empty-plan"><Icon name="list" size={24}/><strong>{item.status === 'stopped' ? 'No accepted plan was saved.' : 'The plan will appear here.'}</strong><p>{item.status === 'stopped' ? 'This task was stopped before a proposal passed the rules checker.' : 'The planner proposes a response when this task gets its first turn. LoopOS checks it before assigning resources.'}</p></div>}</div>
            {item.announcement && <blockquote className="visitor-notice"><span className="eyebrow">PUBLISHED VISITOR NOTICE</span><p>{item.announcement}</p></blockquote>}
            {item.error && <p className="task-error"><Icon name="info"/>{item.error}</p>}
            {item.history.length > 0 && <details className="attempts"><summary>Review plan checks ({item.history.length})</summary>{item.history.map((h, n) => <div key={n}><span className={`badge ${h.accepted ? 'resolved' : 'stopped'}`}>{h.accepted ? 'Accepted' : 'Rejected'}</span><strong>Attempt {n + 1} · {h.source}</strong><p>{h.reason}</p></div>)}</details>}
            {!TERMINAL.includes(item.status) && <details className="stop-task"><summary>Stop this task</summary><p>Releases its volunteers and equipment. This task will not resume; spent credits are not refunded.</p><button className="danger" disabled={disabled} onClick={() => onCommand(`incidents/${item.id}/stop`)}>Stop task and release resources</button></details>}
          </div></aside> : <section className="panel empty-state">Add a situation to see its response plan here.</section>}
        </div></section>
        <section id="activity" className="activity-section"><SectionTitle number="3" title="Understand each decision" subtitle="A timeline of what changed and why. Newest events appear first."/><section className="panel timeline-panel"><div className="timeline-toolbar"><h3>Decision timeline</h3><div className="segmented-control" role="group" aria-label="Filter decision timeline"><button aria-pressed={trailFilter === 'all'} onClick={() => setTrailFilter('all')}>All events</button><button disabled={!item} aria-pressed={trailFilter === 'selected'} onClick={() => setTrailFilter('selected')}>Selected task</button></div></div><div className="events">{events.length ? events.map(e => {
          const related = world.incidents.find(i => i.id === e.loop_id);
          return <article className={`timeline-event ${e.type}`} key={`${world.run_id}-${e.sequence}`}><span className="timeline-marker"/><div><div className="event-heading"><strong>{eventLabel(e.type)}</strong><time dateTime={e.timestamp}>{new Date(e.timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})}</time></div><p>{e.message}</p>{related && <button className="event-task" onClick={() => { onSelect(related.id); document.getElementById('tasks')?.scrollIntoView(); }}>{related.title}<Icon name="arrow" size={14}/></button>}</div></article>;
        }) : <p className="empty-state">No events for this task yet. Give it a turn to see the explanation here.</p>}</div><p className="timeline-foot">{events.length} {events.length === 1 ? 'event' : 'events'} shown · {browserMode ? 'This browser run only' : 'Latest 150 run events'}</p></section></section>
        <section id="learning" className="learning-section"><div className="learning-intro"><span className="resource-icon"><Icon name="book" size={24}/></span><div><h2>The OS ideas, without the jargon</h2><p>LoopOS coordinates tasks; the planner proposes the words. These are separate responsibilities.</p></div></div><details className="panel learning-panel"><summary>Explore scheduling, waiting, and recovery<span>OS concepts</span></summary><div className="learning-body"><div className="concept-grid"><article><span>01 / PRIORITY & AGING</span><h3>Urgent tasks go first</h3><p>A task gets one extra priority point for every three waiting cycles. Longer waits break ties.</p></article><article><span>02 / SAFE PREEMPTION</span><h3>Switch at a safe boundary</h3><p>A task can give way after a scheduler turn finishes. Its saved progress remains available.</p></article><article><span>03 / RESOURCE ALLOCATION</span><h3>No double booking</h3><p>Resources are reserved together. If another task holds what is needed, this task waits.</p></article><article><span>04 / CHECKPOINTS</span><h3>Keep a valid plan</h3><p>{browserMode ? 'The browser demo saves accepted plans. Invalid-plan recovery and watchdog experiments need the full local app.' : 'A rejected proposal cannot change resources. A saved valid plan can be restored; repeated invalid proposals can stop a task.'}</p></article></div>{world.decision && <div className="ranking-section"><h3>Scores at the last scheduling turn</h3><p className="helper-text">These explain the last choice, not a prediction of the next one. Blocked tasks are excluded unless their resources are available.</p><div className="ranking-table"><table><thead><tr><th scope="col">Task</th><th scope="col">Base</th><th scope="col">Waiting bonus</th><th scope="col">Score</th></tr></thead><tbody>{world.decision.candidates.map((c, n) => <tr key={n}><th scope="row">{c.title}{n === 0 && <span>Selected</span>}</th><td>{c.base}</td><td>+{c.age_bonus}</td><td><strong>{c.score}</strong></td></tr>)}</tbody></table></div></div>}{!browserMode && <div className="recovery-tools"><div className="chips"><span>{world.metrics.switches} task switches</span><span>{world.metrics.rejected} rejected proposals</span><span>{world.metrics.recovered} recovered plans</span></div><h3>Try a plan that cannot work</h3><p>Requests two generators when there is only one. Before resources are reserved, see rejection, checkpoint recovery, or the retry watchdog.</p><button disabled={disabled || running || !active.some(i => ['plan', 'reserve'].includes(i.stage))} onClick={() => onCommand('control/invalid-plan')}>Test an invalid plan</button></div>}</div></details></section>
        <details className="panel planner-panel"><summary><span><Icon name="settings"/>Planning settings <small>{planner}</small></span><span>View settings</span></summary><div className="planner-body"><div><h3>{browserMode ? 'A simulation that runs in your browser' : world.mode === 'demo' ? 'Predictable demo plans' : 'AI proposals through local Codex'}</h3><p>{browserMode ? 'No server, sign-in, or API key is needed. Codex and the full recovery tools are available when the local app is running.' : world.mode === 'demo' ? 'Scripted plans make the scenario repeatable. No AI model is called in this mode.' : 'Codex drafts a structured plan and visitor notice. LoopOS checks the proposal and controls scheduling and resources.'}</p>{!browserMode && <><p className="helper-text">Pause before changing planners. ChatGPT sign-in connects local Codex; it is not a login for this website.</p><p className="connection-message">{world.connection.message}</p></>}</div>{!browserMode && <div className="planner-actions"><button disabled={disabled || running} onClick={() => onCommand('codex/check')}>Check Codex sign-in</button><button disabled={disabled || running || (world.mode === 'demo' && !world.connection.signed_in)} onClick={() => onCommand('mode', world.mode === 'demo' ? 'codex' : 'demo')}>Use {world.mode === 'demo' ? 'Codex AI' : 'demo planner'}</button></div>}</div></details>
        <footer><span>LoopOS Crisis Lab</span><p>Fictional coordination exercise · Not emergency advice</p><span>Run {world.run_id}</span></footer>
      </main>
    </div>
  </div>;
}

export default function App() {
  const [world, setWorld] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer;
    async function refresh() {
      try {
        let data;
        if (browserOnly) data = await localDemo.get();
        else {
          const response = await fetch(`${API}/api/crisis/state`);
          if (!response.ok) throw new Error('Server unavailable');
          data = await response.json();
        }
        if (!cancelled) { setWorld(data); setConnectionError(''); }
      } catch {
        if (!cancelled) setConnectionError('Cannot reach the local server. Make sure the backend is running. This page will reconnect automatically.');
      }
      if (!cancelled) timer = setTimeout(refresh, 1000);
    }
    refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  async function command(path, value) {
    setPending(true); setError('');
    try {
      if (browserOnly) {
        const result = await localDemo.post(path, value);
        setWorld(result);
        if (path === 'incidents') setSelected(result.incidents.at(-1)?.id);
        if (path === 'control/reset') setSelected(null);
        return;
      }
      const response = await fetch(`${API}/api/crisis/${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, ...(value ? {body: JSON.stringify({value})} : {})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Action failed');
      setWorld(result);
      if (path === 'incidents') setSelected(result.incidents.at(-1)?.id);
      if (path === 'control/reset') setSelected(null);
    } catch (e) { setError(e.message); }
    finally { setPending(false); }
  }

  if (!world) return <main className="boot"><span className="logo">L</span><h1>Opening your control room</h1><p role={connectionError ? 'alert' : 'status'}>{connectionError || 'Preparing the festival simulation…'}</p><button onClick={() => location.reload()}>Reload dashboard</button></main>;
  return <Dashboard world={world} selected={selected} onSelect={setSelected} onCommand={command} pending={pending || !!connectionError} error={connectionError || error} onDismissError={connectionError ? undefined : () => setError('')} browserMode={browserOnly}/>;
}

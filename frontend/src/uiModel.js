export const TERMINAL = ['resolved', 'stopped'];
export const STAGES = [
  {key: 'plan', label: 'Prepare plan'},
  {key: 'reserve', label: 'Reserve resources'},
  {key: 'respond', label: 'Carry out response'},
  {key: 'announce', label: 'Publish notice'},
];
const NEXT = {
  plan: 'The planner needs a turn to propose a response.',
  reserve: 'The plan is checked. Next, reserve all required resources together.',
  respond: 'The team is carrying out the checked plan. Resources stay reserved.',
  announce: 'The response is finished. Next, publish the notice and release resources.',
};

export function resourceLabel(name, quantity) {
  const labels = {volunteers: ['volunteer', 'Volunteers'], generators: ['generator', 'Generators'], rooms: ['room', 'Rooms']};
  if (!labels[name]) return name;
  return quantity === undefined ? labels[name][1] : quantity === 1 ? labels[name][0] : labels[name][1].toLowerCase();
}

export function taskState(item) {
  const index = Math.max(0, STAGES.findIndex(s => s.key === item.stage));
  const resolved = item.status === 'resolved';
  const stopped = item.status === 'stopped';
  const blocked = item.status === 'blocked';
  return {
    label: resolved ? 'Resolved' : stopped ? 'Stopped' : blocked ? 'Waiting for resources' : item.status === 'responding' ? 'Responding' : 'Ready for a turn',
    heading: resolved ? 'Response completed' : stopped ? 'This task has stopped' : blocked ? 'Why this task is waiting' : 'What happens next',
    next: resolved ? 'Notice published. Resources released for other tasks.' : stopped ? 'This task will not receive more turns. Reserved resources have been released.' : blocked ? 'Another task is using resources this response needs.' : NEXT[item.stage] || 'Waiting for a scheduler turn.',
    steps: STAGES.map((_, n) => resolved || n < index ? 'done' : n === index ? stopped ? 'stopped' : 'current' : 'upcoming'),
  };
}

export function waitingReason(item, world) {
  // Derive current holders from live allocations; stored blocked_by may describe an earlier turn.
  const needs = item.plan?.resources || item.needs;
  const missing = Object.entries(needs).filter(([name, amount]) => amount > (world.resources[name] ?? 0));
  if (!missing.length) return 'The required resources are available now. This task can compete for the next turn; its waiting state updates when selected.';
  const holders = world.incidents.filter(other => other.id !== item.id && missing.some(([name]) => (other.allocation[name] || 0) > 0));
  const resources = missing.map(([name]) => resourceLabel(name).toLowerCase()).join(' and ');
  return holders.length ? `Waiting for ${resources}, currently in use by ${holders.map(other => other.title).join(' and ')}. Those tasks can keep working and release them.` : `Waiting for enough ${resources} to be available. LoopOS will not reserve only part of the request.`;
}

export function schedulerChoice(world) {
  if (!world.decision) return null;
  const selected = world.incidents.find(i => i.id === world.decision.selected);
  const candidate = world.decision.candidates.find(c => c.title === selected?.title) || world.decision.candidates[0];
  if (!candidate) return null;
  return {...candidate, reason: world.decision.candidates.length === 1 ? 'This was the only runnable task at that turn.' : 'This task had the highest scheduling score. Longer waits break ties; tasks waiting for unavailable resources cannot run.'};
}

export function simulationState(world) {
  if (world.busy) return {title: world.status === 'paused' ? 'Pausing at a safe boundary' : 'Finishing a planner turn', description: 'The current turn finishes before another task can be scheduled.'};
  if (world.status === 'running') return {title: 'Simulation running', description: 'Tasks receive turns automatically. Pause to inspect a decision.'};
  if (world.status === 'paused') return {title: 'Simulation paused', description: 'Inspect the tasks below, take one step, or resume.'};
  if (world.status === 'waiting') return {title: 'No runnable tasks', description: 'Open a waiting task to see which resources are unavailable.'};
  if (!world.incidents.some(i => !TERMINAL.includes(i.status))) return {title: 'All tasks finished', description: 'Add another situation to continue, or reset for a fresh simulation.'};
  return {title: 'Ready when you are', description: 'Start the simulation, or use One step to learn at your own pace.'};
}

export function eventLabel(type) {
  return {welcome: 'Simulation ready', arrival: 'Task added', start: 'Simulation started', pause: 'Pause requested', schedule: 'Task selected', switch: 'Task switched', checkpoint: 'Plan checked & saved', waiting: 'Waiting for resources', reserved: 'Resources reserved', working: 'Response in progress', checked: 'Response finished', resolved: 'Task resolved', rejected: 'Plan rejected', recovery: 'Valid plan restored', stopped: 'Task stopped', connection: 'Planner connection', mode: 'Planner changed', error: 'Simulation paused'}[type] || type;
}

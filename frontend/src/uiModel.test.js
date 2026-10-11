import test from 'node:test';
import assert from 'node:assert/strict';
import { createBrowserDemo } from './browserDemo.js';
import { resourceLabel, schedulerChoice, simulationState, taskState, waitingReason } from './uiModel.js';

const incident = (overrides = {}) => ({id: 'rain', title: 'Rain approaching', stage: 'reserve', status: 'queued', needs: {volunteers: 3, generators: 1}, allocation: {}, ...overrides});

test('resolved tasks show four complete stages; stopped tasks do not promise more work', () => {
  assert.deepEqual(taskState(incident({status: 'resolved', stage: 'announce'})).steps, ['done', 'done', 'done', 'done']);
  const stopped = taskState(incident({status: 'stopped', stage: 'respond'}));
  assert.deepEqual(stopped.steps, ['done', 'done', 'stopped', 'upcoming']);
  assert.match(stopped.next, /will not receive more turns/);
});

test('a checked plan is not the same as a resource reservation', () => {
  const view = taskState(incident());
  assert.deepEqual(view.steps, ['done', 'current', 'upcoming', 'upcoming']);
  assert.match(view.next, /Next, reserve/);
});

test('waiting explanations identify holders of missing resources only', () => {
  const item = incident({status: 'blocked'});
  const world = {resources: {volunteers: 4, generators: 0}, incidents: [item,
    incident({id: 'power', title: 'Stage power failure', allocation: {generators: 1}}),
    incident({id: 'supplies', title: 'Supply delivery', allocation: {volunteers: 2}})]};
  const text = waitingReason(item, world);
  assert.match(text, /generators, currently in use by Stage power failure/);
  assert.doesNotMatch(text, /Supply delivery/);
});

test('released resources do not produce stale holder explanations', () => {
  const item = incident({status: 'blocked', blocked_by: ['power']});
  assert.match(waitingReason(item, {resources: {volunteers: 6, generators: 1}, incidents: [item]}), /available now/);
});

test('scheduler choice uses recorded scores, not current waiting age', () => {
  const item = incident({age: 0});
  const choice = schedulerChoice({incidents: [item], decision: {selected: item.id, candidates: [{title: item.title, base: 8, age_bonus: 2, score: 10}]}});
  assert.equal(choice.score, 10);
  assert.equal(choice.age_bonus, 2);
  assert.match(choice.reason, /only runnable task/);
  assert.equal(schedulerChoice({decision: null}), null);
});

test('simulation states and singular resource labels are understandable', () => {
  assert.equal(simulationState({busy: true, status: 'paused'}).title, 'Pausing at a safe boundary');
  assert.equal(simulationState({status: 'completed', incidents: [incident({status: 'resolved'})]}).title, 'All tasks finished');
  assert.equal(simulationState({status: 'waiting'}).title, 'No runnable tasks');
  assert.equal(resourceLabel('generators', 1), 'generator');
  assert.equal(resourceLabel('volunteers', 3), 'volunteers');
});

test('the browser scenario still exposes waiting and runs to completion', async () => {
  const demo = createBrowserDemo();
  await demo.post('control/step');
  await demo.post('incidents', 'power');
  await demo.post('incidents', 'rain');
  let state, waiting = false;
  for (let turn = 0; turn < 60; turn++) {
    state = await demo.post('control/step');
    for (const item of state.incidents) {
      if (item.status === 'blocked') { waiting = true; assert.ok(waitingReason(item, state)); }
      assert.equal(taskState(item).steps.length, 4);
    }
    if (state.status === 'completed') break;
  }
  assert.ok(waiting);
  assert.equal(state.status, 'completed');
  assert.equal(state.metrics.resolved, 3);
  assert.deepEqual(state.resources, state.capacity);
  assert.equal(state.budget, 140);
});

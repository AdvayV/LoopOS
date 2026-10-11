import test, {after, before} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { createBrowserDemo } from '../src/browserDemo.js';

let server, Dashboard;
before(async () => {
  server = await createServer({server: {middlewareMode: true}, appType: 'custom'});
  ({Dashboard} = await server.ssrLoadModule('/src/App.jsx'));
});
after(async () => { if (server) await server.close(); });
const render = (world, props = {}) => renderToStaticMarkup(React.createElement(Dashboard, {world, onSelect: () => {}, onCommand: () => {}, onDismissError: () => {}, ...props}));

test('initial dashboard explains the workflow and exposes accessible controls', async () => {
  const html = render(await createBrowserDemo().get());
  for (const label of ['Festival control room', 'Start simulation', 'One step', 'Add a situation', 'Follow the response', 'Understand each decision', 'Resources required', 'The plan will appear here.']) assert.ok(html.includes(label), label);
  assert.ok(html.includes('Skip to main content'));
  assert.ok(html.includes('aria-label="Simulation controls"'));
  assert.ok(html.includes('aria-current="step"'));
});

test('browser mode hides unsupported recovery and Codex actions', async () => {
  const html = render(await createBrowserDemo().get(), {browserMode: true});
  assert.ok(html.includes('Browser demo'));
  assert.ok(html.includes('full local app'));
  for (const label of ['Test an invalid plan', 'Check Codex sign-in', 'Use Codex AI']) assert.ok(!html.includes(label), label);
});

test('running simulation offers Pause and disables manual stepping', async () => {
  const demo = createBrowserDemo();
  const html = render(await demo.post('control/start'));
  assert.ok(html.includes('Pause simulation'));
  assert.match(html, /<button disabled="" title="Pause the simulation to advance one step at a time\."[^>]*>.*?One step<\/button>/);
});

test('a stopped task without an accepted plan does not promise a future plan', async () => {
  const demo = createBrowserDemo();
  const html = render(await demo.post('incidents/incident-1/stop'));
  assert.ok(html.includes('No accepted plan was saved.'));
  assert.ok(!html.includes('The plan will appear here.'));
  assert.ok(!html.includes('aria-current="step"'));
});

test('resolved tasks show released resources and a published visitor notice', async () => {
  const demo = createBrowserDemo();
  let state;
  for (let n = 0; n < 8; n++) state = await demo.post('control/step');
  const html = render(state);
  assert.ok(html.includes('All tasks finished'));
  assert.ok(html.includes('PUBLISHED VISITOR NOTICE'));
  assert.ok(html.includes('All reserved resources have been released.'));
  assert.ok(!html.includes('aria-current="step"'));
});

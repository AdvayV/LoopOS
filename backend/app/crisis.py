"""Deterministic festival world. Agent text never directly changes resources."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json

CATALOG = {
    'delivery': {'title': 'Prepare tomorrow’s supplies', 'zone': 'Food court', 'priority': 1,
                 'description': 'Organize supplies while the festival is quiet.', 'needs': {'volunteers': 2}, 'cost': 20},
    'power': {'title': 'Stage power failure', 'zone': 'Main stage', 'priority': 10,
              'description': 'The performance has stopped. Arrange temporary power.', 'needs': {'volunteers': 2, 'generators': 1}, 'cost': 80},
    'rain': {'title': 'Rain approaching', 'zone': 'Indoor hall', 'priority': 8,
             'description': 'Move the outdoor workshop to a powered indoor room.', 'needs': {'volunteers': 3, 'generators': 1, 'rooms': 1}, 'cost': 60},
    'crowd': {'title': 'Entrance queue growing', 'zone': 'Entrance', 'priority': 7,
              'description': 'Open a second managed entry lane.', 'needs': {'volunteers': 4}, 'cost': 30},
}
TOTAL = {'volunteers': 6, 'generators': 1, 'rooms': 1}
TERMINAL = {'resolved', 'stopped'}


class CrisisWorld:
    def __init__(self, store):
        self.store = store
        self.run_id = 0
        self.reset()

    def reset(self):
        if self.run_id:
            self.store.set_run_status(self.run_id, 'reset')
        self.run_id = self.store.create_run(datetime.now(UTC).isoformat())
        self.incidents = []
        self.events = []
        self.resources = deepcopy(TOTAL)
        self.budget = 300
        self.tick = 0
        self.status = 'ready'
        self.active = None
        self.last_selected = None
        self.decision = None
        self.metrics = {'resolved': 0, 'switches': 0, 'rejected': 0, 'recovered': 0}
        self.add('delivery')
        self.log('welcome', 'Festival ready. Start the exercise, then add an incident.')

    def log(self, kind, message, incident=None, detail=None):
        event = {'sequence': len(self.events) + 1, 'timestamp': datetime.now(UTC).isoformat(),
                 'type': kind, 'message': message, 'loop_id': incident,
                 'payload': detail or {}}
        self.events.append(event)
        self.store.add_event(self.run_id, event)

    def add(self, kind):
        if kind not in CATALOG:
            raise ValueError('Unknown incident')
        if any(i['kind'] == kind and i['status'] not in TERMINAL for i in self.incidents):
            raise ValueError('This incident is already active')
        if len(self.incidents) >= 30:
            raise ValueError('Reset to begin a new exercise (30 incidents per run).')
        item = dict(deepcopy(CATALOG[kind]), id=f'incident-{len(self.incidents)+1}', kind=kind,
                    status='queued', stage='plan', age=0, attempts=0, plan=None,
                    checkpoint=None, allocation={}, work_left=8 if kind == 'power' else 2, error='', fingerprints=[],
                    history=[], announcement='', blocked_by=[])
        self.incidents.append(item)
        self.log('arrival', f"{item['title']} added with priority {item['priority']}.", item['id'])
        return item

    def get(self, incident_id):
        return next(i for i in self.incidents if i['id'] == incident_id)

    def select(self):
        self.tick += 1
        runnable = [i for i in self.incidents if i['status'] not in TERMINAL]
        for item in runnable:
            item['age'] += 1
            if item['status'] == 'blocked':
                item['blocked_by'] = self.blockers(item)
        candidates = [i for i in runnable if i['status'] != 'blocked' or not self.blockers(i)]
        if not candidates:
            self.status = 'completed' if not runnable else 'waiting'
            return None
        ranked = sorted(candidates, key=lambda i: (i['priority'] + i['age']//3, i['age']), reverse=True)
        selected = ranked[0]
        self.decision = {'selected': selected['id'], 'cycle': self.tick,
                         'candidates': [{'title': i['title'], 'base': i['priority'],
                                         'age_bonus': i['age']//3, 'score': i['priority']+i['age']//3}
                                        for i in ranked]}
        score = selected['priority'] + selected['age']//3
        if self.last_selected and self.last_selected != selected['id']:
            old = self.get(self.last_selected)
            if old['status'] not in TERMINAL:
                self.metrics['switches'] += 1
                self.log('switch', f"Saved {old['title']}; selected {selected['title']} at a safe step boundary.", selected['id'])
        self.log('schedule', f"{selected['title']} selected: base {selected['priority']} + waiting bonus {selected['age']//3} = {score}. Ties favor longer waits.", selected['id'], self.decision)
        selected['age'] = 0
        self.last_selected = selected['id']
        self.active = selected['id']
        return selected

    def demo_plan(self, item, invalid=False):
        return {'summary': f"Coordinate a response to {item['title'].lower()}.",
                'resources': dict(item['needs'], **({'generators': 2} if invalid else {})),
                'cost': item['cost'],
                'steps': ['Assign the response team.', 'Set up the required resources.', 'Confirm the area is ready.'],
                'announcement': f"Festival update: the team is addressing {item['title'].lower()}. Follow volunteer directions."}

    def validate(self, item, plan):
        if not isinstance(plan, dict):
            return 'The plan must be a structured object.'
        if not isinstance(plan.get('summary'), str) or not plan['summary'].strip():
            return 'A plan needs a clear summary.'
        if not isinstance(plan.get('announcement'), str) or not plan['announcement'].strip():
            return 'A plan needs a visitor announcement.'
        steps = plan.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 6 or any(not isinstance(s, str) or not s.strip() for s in steps):
            return 'Provide one to six clear response steps.'
        resources = plan.get('resources')
        if not isinstance(resources, dict) or set(resources) - set(TOTAL):
            return 'Unknown resource in plan.'
        for name, total in TOTAL.items():
            quantity = resources.get(name, 0)
            if type(quantity) is not int or quantity < item['needs'].get(name, 0) or quantity > total:
                return f"Invalid {name} allocation: need at least {item['needs'].get(name, 0)}, maximum {total}."
        if type(plan.get('cost')) is not int or plan['cost'] < item['cost'] or plan['cost'] > self.budget:
            return f"Cost must cover the minimum {item['cost']} and fit remaining budget {self.budget}."
        return ''

    def propose(self, item, plan, source):
        item['attempts'] += 1
        error = self.validate(item, plan)
        item['history'].append({'attempt': item['attempts'], 'source': source, 'accepted': not error,
                                'reason': error or 'Resource quantities, minimum cost and required fields passed.', 'plan': plan})
        if error:
            fingerprint = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            item['fingerprints'].append(fingerprint)
            self.metrics['rejected'] += 1
            item['error'] = error
            self.log('rejected', f"Plan rejected: {error} No resources were changed.", item['id'])
            if item['checkpoint']:
                item['plan'] = deepcopy(item['checkpoint'])
                item['stage'] = 'reserve'
                self.metrics['recovered'] += 1
                self.log('recovery', 'Restored the last valid plan checkpoint.', item['id'])
            elif item['attempts'] >= 3 or item['fingerprints'].count(fingerprint) >= 2:
                self.stop(item, 'Watchdog: repeated invalid plan or three failed attempts.')
            return
        item['plan'] = deepcopy(plan)
        item['checkpoint'] = deepcopy(plan)
        item['stage'] = 'reserve'
        item['status'] = 'queued'
        item['error'] = ''
        self.log('checkpoint', f"{source} plan passed the rules checker. Saved a checkpoint; resources are not yet reserved.", item['id'])

    def blockers(self, item):
        if not item['plan']:
            return []
        needed = item['plan']['resources']
        missing = {k for k, v in needed.items() if v > self.resources[k]}
        return [i['id'] for i in self.incidents if i['id'] != item['id']
                and any(i['allocation'].get(k, 0) > 0 for k in missing)]

    def advance(self, item):
        if item['stage'] == 'reserve':
            error = self.validate(item, item['plan'])
            if error:
                self.stop(item, f'Plan is no longer feasible: {error}')
                return
            blocking = self.blockers(item)
            if blocking:
                item['status'] = 'blocked'
                item['blocked_by'] = blocking
                self.log('waiting', 'Waiting for resources held by: ' + ', '.join(self.get(i)['title'] for i in blocking), item['id'])
                return
            # All quantities are checked before any mutation: no partial reservation.
            for name, quantity in item['plan']['resources'].items():
                if quantity > self.resources[name]:
                    raise RuntimeError('Resource accounting invariant violated')
            item['allocation'] = dict(item['plan']['resources'])
            for name, quantity in item['allocation'].items():
                self.resources[name] -= quantity
            self.budget -= item['plan']['cost']
            item['status'] = 'responding'
            item['stage'] = 'respond'
            item['blocked_by'] = []
            self.log('reserved', 'All required resources reserved together; response can begin.', item['id'])
        elif item['stage'] == 'respond':
            item['work_left'] -= 1
            if item['work_left'] <= 0:
                item['stage'] = 'announce'
                self.log('checked', 'Simulated response finished. Communications can now publish the prepared notice.', item['id'])
            else:
                self.log('working', 'Response team is implementing the checked plan.', item['id'])
        elif item['stage'] == 'announce':
            item['announcement'] = item['plan']['announcement']
            item['status'] = 'resolved'
            self.release(item)
            self.metrics['resolved'] += 1
            self.log('resolved', 'Incident resolved in the simulation. Resources released; visitor notice published.', item['id'])

    def release(self, item):
        for name, quantity in item['allocation'].items():
            self.resources[name] += quantity
        item['allocation'] = {}

    def stop(self, item, reason='Stopped by operator. Reserved resources released.'):
        self.release(item)
        item['status'] = 'stopped'
        item['error'] = reason
        self.log('stopped', reason, item['id'])

    def snapshot(self):
        return deepcopy({'run_id': self.run_id, 'status': self.status, 'tick': self.tick,
                         'active': self.active, 'incidents': self.incidents, 'resources': self.resources,
                         'capacity': TOTAL, 'budget': self.budget, 'events': self.events[-150:][::-1],
                         'metrics': self.metrics, 'decision': self.decision})

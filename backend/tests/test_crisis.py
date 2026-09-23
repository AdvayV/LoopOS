import pytest
from app.crisis import CrisisWorld, TOTAL
from app.database import EventStore


@pytest.fixture
def world(tmp_path):
    return CrisisWorld(EventStore(tmp_path / 'festival.db'))


def test_impossible_plan_cannot_mutate_resources(world):
    item = world.add('power')
    world.propose(item, world.demo_plan(item, invalid=True), 'test')
    assert world.resources == TOTAL
    assert world.budget == 300
    assert item['plan'] is None
    world.propose(item, world.demo_plan(item, invalid=True), 'test')
    assert item['status'] == 'stopped'


def test_exclusive_generator_and_blocked_holder_progress(world):
    power = world.add('power')
    rain = world.add('rain')
    for item in [power, rain]:
        world.propose(item, world.demo_plan(item), 'test')
    world.advance(power)
    world.advance(rain)
    assert rain['status'] == 'blocked'
    assert world.resources['generators'] == 0
    assert world.resources['volunteers'] == 4
    assert world.select()['id'] == power['id']
    while power['status'] != 'resolved':
        world.advance(power)
    world.advance(rain)
    assert rain['status'] == 'responding'
    assert world.resources['generators'] == 0
    assert world.budget == 160


def test_recover_checkpoint(world):
    item = world.add('rain')
    plan = world.demo_plan(item)
    world.propose(item, plan, 'test')
    world.propose(item, world.demo_plan(item, invalid=True), 'test')
    assert item['plan'] == plan
    assert world.metrics['recovered'] == 1
    assert world.resources == TOTAL


def test_urgent_switch_then_background_resumes(world):
    background = world.select()
    world.propose(background, world.demo_plan(background), 'test')
    urgent = world.add('power')
    assert world.select()['id'] == urgent['id']
    assert world.metrics['switches'] == 1
    for _ in range(30):
        item = world.select()
        if not item:
            break
        if item['stage'] == 'plan':
            world.propose(item, world.demo_plan(item), 'test')
        else:
            world.advance(item)
    assert background['status'] == urgent['status'] == 'resolved'
    assert world.resources == TOTAL


def test_stop_releases_resources_but_does_not_refund_spend(world):
    item = world.add('power')
    world.propose(item, world.demo_plan(item), 'test')
    world.advance(item)
    world.stop(item)
    assert world.resources == TOTAL
    assert world.budget == 220


def test_budget_is_revalidated_at_reservation(world):
    item = world.add('power')
    world.propose(item, world.demo_plan(item), 'test')
    world.budget = 10
    world.advance(item)
    assert item['status'] == 'stopped'
    assert world.resources == TOTAL


def test_boolean_resource_and_negative_cost_rejected(world):
    item = world.add('power')
    plan = world.demo_plan(item)
    plan['resources']['generators'] = True
    assert world.validate(item, plan)
    plan = world.demo_plan(item)
    plan['cost'] = -1
    assert world.validate(item, plan)


def test_full_festival_exposes_contention_and_finishes(world):
    world.add('power')
    world.add('rain')
    world.add('crowd')
    for _ in range(100):
        item = world.select()
        if item is None:
            break
        if item['stage'] == 'plan':
            world.propose(item, world.demo_plan(item), 'test')
        else:
            world.advance(item)
        for name, capacity in TOTAL.items():
            assert world.resources[name] + sum(i['allocation'].get(name, 0) for i in world.incidents) == capacity
        assert world.budget >= 0
    assert all(i['status'] == 'resolved' for i in world.incidents)
    assert any(e['type'] == 'waiting' for e in world.events)
    assert world.resources == TOTAL

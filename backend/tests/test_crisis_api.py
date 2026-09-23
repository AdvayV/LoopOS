import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.crisis_api import CrisisService, make_router
from app.database import EventStore


def test_api_demo_lifecycle_and_busy_boundary(tmp_path):
    service = CrisisService(EventStore(tmp_path / 'api.db'))
    app = FastAPI()
    app.include_router(make_router(service))
    with TestClient(app) as client:
        assert client.post('/api/crisis/incidents', json={'value': 'unknown'}).status_code == 409
        assert client.post('/api/crisis/mode', json={'value': 'codex'}).status_code == 409
        assert client.post('/api/crisis/incidents', json={'value': 'power'}).status_code == 200
        assert client.post('/api/crisis/incidents', json={'value': 'power'}).status_code == 409
        response = client.post('/api/crisis/control/step').json()
        power = response['incidents'][-1]
        assert power['plan'] and power['stage'] == 'reserve'
        client.post('/api/crisis/control/step')
        assert service.world.resources['generators'] == 0
        service.busy = True
        assert client.post('/api/crisis/control/reset').status_code == 409
        assert client.post(f"/api/crisis/incidents/{power['id']}/stop").status_code == 409
        assert client.post('/api/crisis/control/pause').status_code == 200
        service.busy = False
        assert client.post(f"/api/crisis/incidents/{power['id']}/stop").status_code == 200
        assert service.world.resources['generators'] == 1
        assert client.post('/api/crisis/control/reset').json()['budget'] == 300


def test_codex_failure_pauses_without_silent_demo_fallback(tmp_path):
    service = CrisisService(EventStore(tmp_path / 'failure.db'))
    service.mode = 'codex'
    def fail(*args):
        raise TimeoutError('No response')
    service.codex.plan = fail
    asyncio.run(service.step())
    assert service.world.status == 'paused'
    assert service.world.incidents[0]['plan'] is None
    assert service.world.events[-1]['type'] == 'connection'
    assert not service.busy

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_initial_state() -> None:
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        state = client.get("/api/state").json()
        assert len(state["loops"]) >= 3


def test_control_endpoints() -> None:
    with TestClient(app) as client:
        assert client.post("/api/control/reset").status_code == 200
        injected = client.post("/api/control/inject-urgent").json()
        assert any(loop["base_priority"] == 10 for loop in injected["loops"])
        assert client.post("/api/control/start").status_code == 200

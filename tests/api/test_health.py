"""Application-composition smoke tests."""

from fastapi.testclient import TestClient

from automation_foundry.api import create_app


def test_shared_and_lane_health_routes() -> None:
    client = TestClient(create_app())

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/authoring/health").json() == {"status": "ok", "subsystem": "authoring"}
    assert client.get("/api/execution/health").json() == {"status": "ok", "subsystem": "execution"}

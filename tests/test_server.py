"""Tests for the server adapter (auth / AskUser answer)."""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")


from nono_agent.adapters.server import create_server_app  # noqa: E402


def _client(**kwargs):
    app = create_server_app(**kwargs)
    return fastapi_testclient.TestClient(app)


class TestAuth:
    def test_health_unauthenticated(self):
        with _client(api_key="secret") as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_protected_requires_key(self):
        with _client(api_key="secret") as client:
            resp = client.get("/sessions")
            assert resp.status_code == 401

    def test_protected_with_key(self):
        with _client(api_key="secret") as client:
            resp = client.get("/sessions", headers={"X-API-Key": "secret"})
            assert resp.status_code == 200

    def test_protected_with_bearer(self):
        with _client(api_key="secret") as client:
            resp = client.get("/sessions", headers={"Authorization": "Bearer secret"})
            assert resp.status_code == 200

    def test_no_auth_when_no_key(self):
        with _client(api_key=None) as client:
            resp = client.get("/sessions")
            assert resp.status_code == 200


class TestAskUserAnswer:
    def test_no_pending_question(self):
        with _client(api_key=None) as client:
            resp = client.post("/session/x/answer", json={"answers": {}})
            assert resp.status_code == 404

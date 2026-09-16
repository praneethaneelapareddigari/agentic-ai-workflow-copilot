import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db
from app.main import app


@pytest.fixture(autouse=True, scope="module")
async def _init_db():
    await init_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthEndpoint:
    async def test_health_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["llm_mode"] == "mock"


class TestWorkflowEndpoints:
    async def test_create_workflow_returns_completed_status(self, client):
        resp = await client.post("/api/v1/workflows", json={"message": "weather in Prague"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "completed"
        assert len(body["steps"]) == 8
        assert len(body["tool_calls"]) >= 1

    async def test_get_workflow_by_id(self, client):
        create_resp = await client.post("/api/v1/workflows", json={"message": "weather in Krakow"})
        workflow_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/v1/workflows/{workflow_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == workflow_id

    async def test_get_nonexistent_workflow_404s(self, client):
        resp = await client.get("/api/v1/workflows/does-not-exist")
        assert resp.status_code == 404

    async def test_list_workflows(self, client):
        await client.post("/api/v1/workflows", json={"message": "weather in Bergen"})
        resp = await client.get("/api/v1/workflows")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 1

    async def test_empty_message_rejected(self, client):
        resp = await client.post("/api/v1/workflows", json={"message": ""})
        assert resp.status_code == 422

    async def test_metrics_endpoint_reports_counters(self, client):
        await client.post("/api/v1/workflows", json={"message": "weather in Nice"})
        resp = await client.get("/api/v1/workflows/_meta/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "counters" in body
        assert "histograms" in body


class TestConversationEndpoints:
    async def test_conversation_persists_across_workflows(self, client):
        r1 = await client.post("/api/v1/workflows", json={"message": "weather in Malta"})
        conv_id = r1.json()["conversation_id"]

        r2 = await client.post(
            "/api/v1/workflows",
            json={"conversation_id": conv_id, "message": "and what about Cyprus"},
        )
        assert r2.json()["conversation_id"] == conv_id

        conv_resp = await client.get(f"/api/v1/conversations/{conv_id}")
        assert conv_resp.status_code == 200
        assert len(conv_resp.json()["messages"]) == 4  # 2 user + 2 assistant

from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_root_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "leadforge-backend"
    assert data["app_name"] == "LeadForge"
    assert "timestamp" in data
    assert "runtime" in data
    assert "fastapi_version" in data["runtime"]
    assert data["database"]["connected"] is True
    assert data["database"]["foreign_keys_enforced"] is True
    assert set(data["database"]["tables"]) == {
        "lead_analyses",
        "leads",
        "search_run_leads",
        "search_runs",
    }


def test_api_health_endpoint() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "leadforge-backend"
    assert data["config"]["ai_provider"] in ("mock", "gemini")
    assert data["config"]["database_engine"] == "SQLite (SQLAlchemy ORM)"


def test_foundation_endpoint_does_not_leak_secrets() -> None:
    response = client.get("/api/v1/foundation")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["current_phase"] == 4
    assert len(data["architecture_flow"]) == 6
    raw_text = response.text.lower()
    assert "gemini_api_key" not in raw_text
    assert "my_gemini_api_key" not in raw_text
    assert "n8n_callback_secret" not in raw_text


from collections.abc import Generator
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.db import create_sqlite_engine, get_db, init_db
from backend.app.main import app
from backend.app.models import (
    Lead,
    LeadAnalysis,
    SearchRun,
    SearchRunLead,
    SearchRunStatus,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_WORKFLOW_PATH = WORKSPACE_ROOT / "public/assets/aistudio/My_workflow_2__1__aebf1ca6.json"
FASTAPI_WORKFLOW_PATH = (
    WORKSPACE_ROOT / "backend/workflows/leadforge_discovery_qualification_v2_fastapi.json"
)
TEST_CALLBACK_SECRET = "test-phase4-super-secret-token"


@pytest.fixture()
def n8n_test_engine(tmp_path: Path) -> Engine:
    db_file = tmp_path / "test_n8n_leadforge.db"
    test_engine = create_sqlite_engine(f"sqlite:///{db_file}")
    init_db(test_engine)
    return test_engine


@pytest.fixture()
def n8n_db_session(n8n_test_engine: Engine) -> Generator[Session, None, None]:
    TestingSessionLocal = sessionmaker(
        bind=n8n_test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def n8n_client(
    n8n_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("N8N_CALLBACK_SECRET", TEST_CALLBACK_SECRET)
    monkeypatch.setenv("N8N_WEBHOOK_URL", "")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "")
    monkeypatch.setenv("DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("DISCOVERY_EXECUTION_MODE", "n8n")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    get_settings.cache_clear()

    def override_get_db() -> Generator[Session, None, None]:
        yield n8n_db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_local_same_machine_dispatch_without_backend_public_url(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify that when BACKEND_PUBLIC_URL is unset/empty in local development,
    FastAPI automatically generates http://127.0.0.1:8001/api/v1/integrations/n8n/callback
    and dispatches to local n8n (http://127.0.0.1:5678/webhook/leadforge).
    """
    monkeypatch.delenv("BACKEND_PUBLIC_URL", raising=False)
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://127.0.0.1:5678/webhook/leadforge")
    monkeypatch.setenv("DEPLOYMENT_MODE", "local")
    get_settings.cache_clear()

    captured_requests: list[dict[str, Any]] = []

    def mock_dispatch(webhook_url: str, payload: dict[str, Any], timeout_seconds: float) -> httpx.Response:
        captured_requests.append({"url": webhook_url, "json": payload, "timeout": timeout_seconds})
        return httpx.Response(200, json={"status": "Execution started"})

    monkeypatch.setattr("backend.app.services.n8n_service.dispatch_webhook_post", mock_dispatch)

    res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Dental Clinic", "max_results": 10},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "running"

    assert len(captured_requests) == 1
    dispatched = captured_requests[0]
    assert dispatched["url"] == "http://127.0.0.1:5678/webhook/leadforge"
    assert dispatched["json"] == {
        "search_run_id": body["id"],
        "city": "Austin",
        "business_type": "Dental Clinic",
        "max_results": 10,
        "callback_url": "http://127.0.0.1:8001/api/v1/integrations/n8n/callback",
    }


def test_outbound_webhook_dispatch_success(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify POST /api/v1/search-runs dispatches trusted payload to n8n and transitions to running."""
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n-tunnel.example.com/webhook/leadforge")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://leadforge-api.example.run.app")
    monkeypatch.setenv("DEPLOYMENT_MODE", "hybrid_cloud")
    get_settings.cache_clear()

    captured_requests: list[dict[str, Any]] = []

    def mock_dispatch(webhook_url: str, payload: dict[str, Any], timeout_seconds: float) -> httpx.Response:
        captured_requests.append({"url": webhook_url, "json": payload, "timeout": timeout_seconds})
        return httpx.Response(200, json={"status": "Execution started"})

    monkeypatch.setattr("backend.app.services.n8n_service.dispatch_webhook_post", mock_dispatch)

    res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Dental Clinic", "max_results": 15},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "running"
    assert body["completed_at"] is None

    assert len(captured_requests) == 1
    dispatched = captured_requests[0]
    assert dispatched["url"] == "https://n8n-tunnel.example.com/webhook/leadforge"
    assert dispatched["json"] == {
        "search_run_id": body["id"],
        "city": "Austin",
        "business_type": "Dental Clinic",
        "max_results": 15,
        "callback_url": "https://leadforge-api.example.run.app/api/v1/integrations/n8n/callback",
    }


def test_outbound_webhook_rejects_untrusted_frontend_callback_url(
    n8n_client: TestClient,
) -> None:
    """Verify frontend cannot supply or override callback_url when creating a search run."""
    res = n8n_client.post(
        "/api/v1/search-runs",
        json={
            "city": "Austin",
            "business_type": "Dental Clinic",
            "max_results": 10,
            "callback_url": "https://attacker.example.com/steal-leads",
        },
    )
    assert res.status_code == 422


def test_outbound_webhook_failure_marks_search_run_failed(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify network errors or non-2xx responses from n8n transition SearchRun to failed."""
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n-tunnel.example.com/webhook/leadforge")
    get_settings.cache_clear()

    def mock_dispatch_timeout(
        webhook_url: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> httpx.Response:
        raise httpx.ConnectTimeout("Timed out connecting to n8n tunnel")

    monkeypatch.setattr("backend.app.services.n8n_service.dispatch_webhook_post", mock_dispatch_timeout)

    res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Denver", "business_type": "Roofing", "max_results": 10},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "failed"
    assert body["completed_at"] is not None


def test_placeholder_urls_rejected_as_callback_or_webhook_destinations(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify template placeholder strings are never used as real callback or webhook destinations."""
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://127.0.0.1:5678/webhook/leadforge")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "MY_APP_URL")
    get_settings.cache_clear()

    res_placeholder_cb = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Plumber", "max_results": 5},
    )
    assert res_placeholder_cb.status_code == 201
    assert res_placeholder_cb.json()["status"] == "failed"

    monkeypatch.setenv("BACKEND_PUBLIC_URL", "")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://<your-n8n-https-tunnel>/webhook/leadforge")
    get_settings.cache_clear()

    res_placeholder_wh = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Plumber", "max_results": 5},
    )
    assert res_placeholder_wh.status_code == 201
    assert res_placeholder_wh.json()["status"] == "failed"


def test_hybrid_deployment_constraints_reject_loopback_and_preview_urls(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify hybrid_cloud deployment rejects 127.0.0.1 webhook URLs and session-gated preview callback URLs."""
    # Case 1: 127.0.0.1 webhook URL in hybrid_cloud mode must be rejected before dispatch
    monkeypatch.setenv("DEPLOYMENT_MODE", "hybrid_cloud")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://127.0.0.1:5678/webhook/leadforge")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://leadforge-api.example.run.app")
    get_settings.cache_clear()

    res_loopback = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Miami", "business_type": "Med Spa", "max_results": 5},
    )
    assert res_loopback.status_code == 201
    assert res_loopback.json()["status"] == "failed"

    # Case 2: Session-gated AI Studio preview URL (ais-dev-*.run.app) as callback URL must be rejected
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n-tunnel.example.com/webhook/leadforge")
    monkeypatch.setenv(
        "BACKEND_PUBLIC_URL",
        "https://ais-dev-22hw4764cwmpv4lmuqibaj-371598352702.europe-west1.run.app",
    )
    get_settings.cache_clear()

    res_preview = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Miami", "business_type": "Med Spa", "max_results": 5},
    )
    assert res_preview.status_code == 201
    assert res_preview.json()["status"] == "failed"

    # Verify SQLite persistence check clearly distinguishes local ephemeral vs mounted volume
    settings = get_settings()
    persistence = settings.get_sqlite_persistence_status()
    assert persistence["is_production_persistent"] is False
    assert persistence["storage_mode"] == "local_ephemeral_filesystem"
    assert "ephemeral" in str(persistence["persistence_warning"]).lower()


def test_docker_compose_url_validation_accepted_and_rejected_cases(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify DEPLOYMENT_MODE=docker_compose URL validation rules:
    1. Accepts recognized internal Docker HTTP service hostnames (e.g. http://n8n:5678/webhook/leadforge
       and http://leadforge-app:3000) even when APP_ENV=production.
    2. Accepts valid external HTTPS URLs in docker_compose mode.
    3. Rejects 127.0.0.1 and localhost for both webhook and callback URLs in docker_compose mode.
    4. Rejects arbitrary external HTTP URLs (e.g. http://example.com, http://n8nsalim.onrender.com).
    5. Preserves strict HTTPS-only requirement when DEPLOYMENT_MODE=hybrid_cloud and APP_ENV=production.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "docker_compose")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/leadforge")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "http://leadforge-app:3000")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.deployment_mode == "docker_compose"
    wh_ok, wh_err = settings.validate_webhook_url()
    cb_ok, cb_err = settings.validate_callback_url_for_hybrid()
    assert wh_ok is True and wh_err is None
    assert cb_ok is True and cb_err is None
    assert (
        settings.get_trusted_callback_url()
        == "http://leadforge-app:3000/api/v1/integrations/n8n/callback"
    )

    # Verify dispatch succeeds in docker_compose mode with recognized internal HTTP hostnames
    captured_requests: list[dict[str, Any]] = []

    def mock_dispatch(webhook_url: str, payload: dict[str, Any], timeout_seconds: float) -> httpx.Response:
        captured_requests.append({"url": webhook_url, "json": payload, "timeout": timeout_seconds})
        return httpx.Response(200, json={"status": "Execution started"})

    monkeypatch.setattr("backend.app.services.n8n_service.dispatch_webhook_post", mock_dispatch)

    res_ok = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Dental Clinic", "max_results": 5},
    )
    assert res_ok.status_code == 201
    assert res_ok.json()["status"] == "running"
    assert len(captured_requests) == 1
    assert captured_requests[0]["url"] == "http://n8n:5678/webhook/leadforge"
    assert (
        captured_requests[0]["json"]["callback_url"]
        == "http://leadforge-app:3000/api/v1/integrations/n8n/callback"
    )

    # Reject loopback (127.0.0.1 / localhost) in docker_compose mode
    for loopback_wh in ("http://127.0.0.1:5678/webhook/leadforge", "http://localhost:5678/webhook/leadforge"):
        monkeypatch.setenv("N8N_WEBHOOK_URL", loopback_wh)
        monkeypatch.setenv("BACKEND_PUBLIC_URL", "http://leadforge-app:3000")
        get_settings.cache_clear()
        wh_valid, _ = get_settings().validate_webhook_url()
        assert wh_valid is False

    for loopback_cb in ("http://127.0.0.1:8001", "http://localhost:3000"):
        monkeypatch.setenv("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/leadforge")
        monkeypatch.setenv("BACKEND_PUBLIC_URL", loopback_cb)
        get_settings.cache_clear()
        cb_valid, _ = get_settings().validate_callback_url_for_hybrid()
        assert cb_valid is False

    # Reject arbitrary external HTTP URLs in docker_compose mode
    for untrusted_http_wh in (
        "http://example.com/webhook/leadforge",
        "http://n8nsalim.onrender.com/webhook/leadforge",
        "http://198.51.100.10:5678/webhook/leadforge",
    ):
        monkeypatch.setenv("N8N_WEBHOOK_URL", untrusted_http_wh)
        monkeypatch.setenv("BACKEND_PUBLIC_URL", "http://leadforge-app:3000")
        get_settings.cache_clear()
        wh_valid, _ = get_settings().validate_webhook_url()
        assert wh_valid is False

    for untrusted_http_cb in (
        "http://example.com",
        "http://leadforge.example.com:3000",
        "http://203.0.113.5:3000",
    ):
        monkeypatch.setenv("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/leadforge")
        monkeypatch.setenv("BACKEND_PUBLIC_URL", untrusted_http_cb)
        get_settings.cache_clear()
        cb_valid, _ = get_settings().validate_callback_url_for_hybrid()
        assert cb_valid is False

    # Confirm hybrid_cloud + production STILL rejects internal HTTP URLs (requires HTTPS)
    monkeypatch.setenv("DEPLOYMENT_MODE", "hybrid_cloud")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/leadforge")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "http://leadforge-app:3000")
    get_settings.cache_clear()
    hybrid_settings = get_settings()
    assert hybrid_settings.validate_webhook_url()[0] is False
    assert hybrid_settings.validate_callback_url_for_hybrid()[0] is False



def test_callback_authentication_and_missing_search_run(
    n8n_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify 401 on missing header, 403 on wrong secret, 503 when secret unconfigured, and 404 on unknown SearchRun."""
    # Create a pending search run
    run_res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Dental Clinic", "max_results": 10},
    )
    run_id = run_res.json()["id"]

    # 1. Missing X-Callback-Secret -> 401
    res_401 = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        json={"search_run_id": run_id, "status": "completed", "leads": []},
    )
    assert res_401.status_code == 401

    # 2. Invalid X-Callback-Secret -> 403
    res_403 = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": "wrong-secret"},
        json={"search_run_id": run_id, "status": "completed", "leads": []},
    )
    assert res_403.status_code == 403

    # 3. Unknown search_run_id with valid secret -> 404
    res_404 = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json={"search_run_id": 999999, "status": "completed", "leads": []},
    )
    assert res_404.status_code == 404

    # 4. Unconfigured server secret -> 503
    monkeypatch.setenv("N8N_CALLBACK_SECRET", "")
    get_settings.cache_clear()
    res_503 = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json={"search_run_id": run_id, "status": "completed", "leads": []},
    )
    assert res_503.status_code == 503


def test_callback_persists_both_website_branches_raw_payloads_and_ai_failures(
    n8n_client: TestClient,
) -> None:
    """
    Verify batch callback handles:
    1. Business WITH website + full Stage 1 & Stage 2 AI outputs (preserving canonical Apify title over AI company_name).
    2. Business WITHOUT website + Stage 1 ('high' confidence string) & Stage 2 AI outputs.
    3. Business where AI analysis failed (`analysis: null`, missing optional fields) — Lead is still persisted.
    """
    run_res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Austin", "business_type": "Dental Clinic", "max_results": 20},
    )
    run_id = run_res.json()["id"]

    callback_payload = {
        "search_run_id": run_id,
        "status": "completed",
        "execution_id": "exec-austin-001",
        "leads": [
            {
                "company_name": "AI Shortened Name That Must Not Overwrite Apify",
                "title": "Austin Premier Dental Studio PLLC",
                "place_id": "ChIJ_austin_premier_001",
                "website": "https://www.austinpremierdental.example.com/",
                "address": "200 Congress Ave, Austin, TX 78701",
                "phone": "+1-512-555-0199",
                "city": "Austin",
                "business_type": "Dental Clinic",
                "rating": 4.9,
                "reviews_count": 312,
                "processing_status": "completed",
                "analysis": {
                    "ai_company_name": "Austin Premier Dental",
                    "business_summary": "Full-service cosmetic and implant dental practice in downtown Austin.",
                    "personalization_observation": "Offers 3D CBCT imaging and same-day crowns on homepage.",
                    "potential_opportunity": "Online appointment booking currently requires a phone call back.",
                    "icebreaker": "Noticed your Austin studio highlights same-day crowns and 3D imaging.",
                    "confidence": "high",
                    "stage1_analysis": {
                        "business_summary": "Full-service cosmetic and implant dental practice in downtown Austin.",
                        "website_status": "active",
                        "confidence": "high",
                    },
                    "stage2_analysis": {
                        "company_name": "Austin Premier Dental",
                        "personalization_observation": "Offers 3D CBCT imaging and same-day crowns on homepage.",
                        "potential_opportunity": "Online appointment booking currently requires a phone call back.",
                        "icebreaker": "Noticed your Austin studio highlights same-day crowns and 3D imaging.",
                    },
                },
                "raw_payload": {
                    "apify": {
                        "title": "Austin Premier Dental Studio PLLC",
                        "placeId": "ChIJ_austin_premier_001",
                        "totalScore": 4.9,
                        "reviewsCount": 312,
                        "website": "https://www.austinpremierdental.example.com/",
                        "address": "200 Congress Ave, Austin, TX 78701",
                        "phone": "+1-512-555-0199",
                        "categories": ["Dental clinic", "Cosmetic dentist"],
                        "openingHours": [{"day": "Monday", "hours": "8 AM to 5 PM"}],
                    },
                    "stage1_ai": {
                        "business_summary": "Full-service cosmetic and implant dental practice in downtown Austin.",
                        "website_status": "active",
                        "confidence": "high",
                    },
                    "stage2_ai": {
                        "company_name": "Austin Premier Dental",
                        "personalization_observation": "Offers 3D CBCT imaging and same-day crowns on homepage.",
                        "potential_opportunity": "Online appointment booking currently requires a phone call back.",
                        "icebreaker": "Noticed your Austin studio highlights same-day crowns and 3D imaging.",
                    },
                },
            },
            {
                "title": "South Lamar Family Dentistry (No Website)",
                "place_id": "ChIJ_south_lamar_002",
                "website": None,
                "address": "1400 S Lamar Blvd, Austin, TX 78704",
                "phone": "+1-512-555-0244",
                "city": "Austin",
                "business_type": "Dental Clinic",
                "rating": 4.6,
                "reviews_count": 88,
                "processing_status": "completed",
                "analysis": {
                    "ai_company_name": "South Lamar Dentistry",
                    "business_summary": "Established neighborhood family dentist on South Lamar without a web domain.",
                    "personalization_observation": "88 Google reviews with a 4.6 rating but no listed website.",
                    "potential_opportunity": "Launching a dedicated patient intake website to capture local search traffic.",
                    "icebreaker": "Saw your 4.6 rating across 88 reviews on South Lamar.",
                    "confidence": "medium",
                },
                "raw_payload": {
                    "apify": {
                        "title": "South Lamar Family Dentistry (No Website)",
                        "placeId": "ChIJ_south_lamar_002",
                        "totalScore": 4.6,
                        "reviewsCount": 88,
                        "website": None,
                    },
                    "stage1_ai": {"website_status": "missing", "confidence": "medium"},
                    "stage2_ai": {"company_name": "South Lamar Dentistry"},
                },
            },
            {
                "title": "Eastside Emergency Dental Care",
                "place_id": "ChIJ_eastside_003",
                "website": None,
                "address": None,
                "phone": None,
                "city": None,
                "business_type": None,
                "rating": None,
                "reviews_count": None,
                "processing_status": "partial_ai_failure",
                "analysis": None,
                "raw_payload": {
                    "apify": {
                        "title": "Eastside Emergency Dental Care",
                        "placeId": "ChIJ_eastside_003",
                    },
                    "stage1_ai": {"error": "Gemini quota exceeded"},
                    "stage2_ai": {"error": "Stage 2 skipped"},
                },
            },
        ],
    }

    cb_res = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json=callback_payload,
    )
    assert cb_res.status_code == 200
    cb_data = cb_res.json()
    assert cb_data["status"] == "completed"
    assert cb_data["leads_received"] == 3
    assert cb_data["leads_created"] == 3
    assert cb_data["leads_updated"] == 0
    assert cb_data["leads_linked"] == 3
    assert cb_data["analyses_upserted"] == 2

    # Verify SearchRun is now completed with all 3 leads
    run_detail = n8n_client.get(f"/api/v1/search-runs/{run_id}").json()
    assert run_detail["status"] == "completed"
    assert run_detail["completed_at"] is not None
    assert run_detail["leads_count"] == 3

    leads_by_name = {lead["company_name"]: lead for lead in run_detail["leads"]}
    # 1. Canonical Apify title must be used, NOT the AI-shortened name
    assert "Austin Premier Dental Studio PLLC" in leads_by_name
    assert "AI Shortened Name That Must Not Overwrite Apify" not in leads_by_name
    lead1 = leads_by_name["Austin Premier Dental Studio PLLC"]
    assert lead1["rating"] == 4.9
    assert lead1["reviews_count"] == 312
    assert lead1["analysis"]["confidence"] == 0.9

    # Inspect Lead 1 raw_payload via GET /api/v1/leads/{id}
    lead1_detail = n8n_client.get(f"/api/v1/leads/{lead1['id']}").json()
    raw1 = json.loads(lead1_detail["raw_payload"])
    assert raw1["apify"]["title"] == "Austin Premier Dental Studio PLLC"
    assert raw1["apify"]["openingHours"] == [{"day": "Monday", "hours": "8 AM to 5 PM"}]
    assert raw1["stage1_ai"]["website_status"] == "active"
    assert raw1["stage2_ai"]["company_name"] == "Austin Premier Dental"
    assert raw1["ai_company_name"] == "Austin Premier Dental"

    # 2. No-website business preserved with confidence "medium" -> 0.6
    lead2 = leads_by_name["South Lamar Family Dentistry (No Website)"]
    assert lead2["website"] is None
    assert lead2["analysis"]["confidence"] == 0.6

    # 3. AI-failure business preserved with fallback city/business_type and analysis=None
    lead3 = leads_by_name["Eastside Emergency Dental Care"]
    assert lead3["city"] == "Austin"
    assert lead3["business_type"] == "Dental Clinic"
    assert lead3["analysis"] is None


def test_deduplication_priority_and_no_key_retention_and_idempotent_retries(
    n8n_client: TestClient,
) -> None:
    """
    Verify exact deduplication priority:
    1. Apify placeId
    2. Normalized website
    3. Exact normalized company_name + full address
    4. If no reliable key exists (no placeId, no website, no address), retain the record.
    5. Retrying the exact same callback is 100% idempotent.
    """
    run1_id = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Seattle", "business_type": "Coffee Roaster", "max_results": 20},
    ).json()["id"]

    batch1 = {
        "search_run_id": run1_id,
        "status": "completed",
        "leads": [
            {
                "title": "Sound Wave Coffee",
                "place_id": "ChIJ_sound_wave_100",
                "website": "https://www.SoundWaveCoffee.com/",
                "address": "100 Pike St, Seattle, WA",
                "rating": 4.7,
                "reviews_count": 120,
            },
            {
                "title": "Emerald City Espresso",
                "place_id": None,
                "website": "https://www.EmeraldEspresso.example.com/menu/",
                "address": "250 Pine St, Seattle, WA",
                "rating": 4.5,
                "reviews_count": 64,
            },
            {
                "title": "Rainier Artisanal Roasters",
                "place_id": None,
                "website": None,
                "address": "500 1st Ave S, Seattle, WA",
                "rating": 4.8,
                "reviews_count": 90,
            },
            {
                "title": "Pop-Up Coffee Cart",
                "place_id": None,
                "website": None,
                "address": None,
                "rating": 5.0,
                "reviews_count": 12,
            },
            {
                "title": "Pop-Up Coffee Cart",
                "place_id": None,
                "website": None,
                "address": None,
                "rating": 4.3,
                "reviews_count": 7,
            },
        ],
    }

    res1 = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json=batch1,
    )
    assert res1.status_code == 200
    # Note: the two "Pop-Up Coffee Cart" items have no placeId, no website, and no address,
    # so both must be retained (5 leads created total).
    assert res1.json()["leads_created"] == 5

    # Retry the exact same callback for run1_id -> must update in-place with 0 new leads created
    res1_retry = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json=batch1,
    )
    assert res1_retry.status_code == 200
    assert res1_retry.json()["leads_created"] == 0
    assert res1_retry.json()["leads_updated"] == 5

    # Now create a second SearchRun that discovers overlapping businesses matching by:
    # - Priority 1 (placeId): "Sound Wave Coffee Roasters" (different name/website, same placeId)
    # - Priority 2 (normalized website): "Emerald Espresso Bar" (no placeId, website http://emeraldespresso.example.com/menu)
    # - Priority 3 (normalized name + address): "  rainier   artisanal roasters " at "500 1st ave s, seattle, wa"
    # - Priority 4 (no key): "Pop-Up Coffee Cart" with no placeId/website/address -> must be retained as a NEW record!
    run2_id = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Seattle", "business_type": "Cafe", "max_results": 20},
    ).json()["id"]

    batch2 = {
        "search_run_id": run2_id,
        "status": "completed",
        "leads": [
            {
                "title": "Sound Wave Coffee Roasters",
                "place_id": "ChIJ_sound_wave_100",
                "website": "https://newdomain-soundwave.example.com",
                "address": "999 Different Ave, Seattle, WA",
                "rating": 4.8,
                "reviews_count": 140,
            },
            {
                "title": "Emerald Espresso Bar",
                "place_id": None,
                "website": "http://emeraldespresso.example.com/menu",
                "address": "888 Other Rd, Seattle, WA",
                "rating": 4.6,
                "reviews_count": 70,
            },
            {
                "title": "  rainier   artisanal roasters ",
                "place_id": None,
                "website": None,
                "address": "500   1st Ave S, Seattle, WA ",
                "rating": 4.9,
                "reviews_count": 105,
            },
            {
                "title": "Pop-Up Coffee Cart",
                "place_id": None,
                "website": None,
                "address": None,
                "rating": 4.1,
                "reviews_count": 3,
            },
        ],
    }

    res2 = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json=batch2,
    )
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["leads_updated"] == 3
    assert body2["leads_created"] == 1
    assert body2["leads_linked"] == 4

    all_leads = n8n_client.get("/api/v1/leads?limit=50").json()
    assert all_leads["total"] == 6


def test_atomic_transaction_rollback_on_mid_batch_error(
    n8n_client: TestClient,
    n8n_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that if an unexpected error occurs mid-batch, the entire transaction rolls back cleanly."""
    run_res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "Chicago", "business_type": "Law Firm", "max_results": 10},
    )
    run_id = run_res.json()["id"]

    original_flush = n8n_db_session.flush
    flush_calls = 0

    def failing_flush(*args: Any, **kwargs: Any) -> None:
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls >= 3:
            raise RuntimeError("Simulated mid-batch SQLite disk error")
        original_flush(*args, **kwargs)

    monkeypatch.setattr(n8n_db_session, "flush", failing_flush)

    payload = {
        "search_run_id": run_id,
        "status": "completed",
        "leads": [
            {"title": "Lakefront Legal Group", "place_id": "ChIJ_chi_1"},
            {"title": "Windy City Attorneys", "place_id": "ChIJ_chi_2"},
        ],
    }

    res = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json=payload,
    )
    assert res.status_code == 500
    assert "rolled back" in res.json()["detail"].lower()

    # Verify zero partial leads or links were persisted and SearchRun is still pending
    monkeypatch.undo()
    leads_count = len(n8n_db_session.scalars(select(Lead)).all())
    links_count = len(n8n_db_session.scalars(select(SearchRunLead)).all())
    run_obj = n8n_db_session.scalar(select(SearchRun).where(SearchRun.id == run_id))
    assert leads_count == 0
    assert links_count == 0
    assert run_obj is not None
    assert run_obj.status == SearchRunStatus.PENDING.value


def test_workflow_json_integrity_and_concurrent_execution_isolation() -> None:
    """
    Verify:
    1. Original workflow JSON is untouched.
    2. Corrected workflow JSON does NOT use $getWorkflowStaticData('global').
    3. Executing the corrected workflow JS nodes across two interleaved concurrent searches
       proves that their leads never mix.
    """
    assert ORIGINAL_WORKFLOW_PATH.exists()
    assert FASTAPI_WORKFLOW_PATH.exists()

    orig_raw = ORIGINAL_WORKFLOW_PATH.read_text(encoding="utf-8")
    orig_json = json.loads(orig_raw)
    assert orig_json["name"] == "My workflow 2"
    assert len(orig_json["nodes"]) == 14

    corr_raw = FASTAPI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "$getWorkflowStaticData" not in corr_raw
    assert TEST_CALLBACK_SECRET not in corr_raw

    corr_json = json.loads(corr_raw)
    nodes_by_name = {node["name"]: node for node in corr_json["nodes"]}

    assert "Webhook Trigger" in nodes_by_name
    assert "Normalize Trigger Input" in nodes_by_name
    assert "Build Batch Callback Payload" in nodes_by_name
    assert "Send FastAPI Callback" in nodes_by_name
    assert nodes_by_name["scraper"].get("onError") == "continueRegularOutput"
    assert nodes_by_name["Basic LLM Chain"].get("onError") == "continueRegularOutput"
    assert nodes_by_name["Message a model"].get("onError") == "continueRegularOutput"
    assert nodes_by_name["Append row in sheet"].get("onError") == "continueRegularOutput"

    # Execute the actual JavaScript code from the workflow nodes in Node.js for two concurrent executions
    dedup_js = nodes_by_name["Code in JavaScript"]["parameters"]["jsCode"]
    parse_ai_js = nodes_by_name["Code in JavaScript1"]["parameters"]["jsCode"]
    build_batch_js = nodes_by_name["Build Batch Callback Payload"]["parameters"]["jsCode"]

    node_harness = """
const dedupCode = process.env.DEDUP_JS;
const parseAiCode = process.env.PARSE_AI_JS;
const buildBatchCode = process.env.BUILD_BATCH_JS;

function runExecutionSimulation(triggerInput, apifyItems, aiResponsesByPlaceId) {
  // 1. Run 'Code in JavaScript' (deduplication & field normalization)
  const dedupFn = new Function('$input', '$', dedupCode);
  const $lookupForDedup = (nodeName) => {
    if (nodeName === 'Normalize Trigger Input') {
      return { first: () => ({ json: triggerInput }) };
    }
    throw new Error('Unexpected node lookup: ' + nodeName);
  };
  const dedupedItems = dedupFn(
    { all: () => apifyItems.map((x) => ({ json: x })) },
    $lookupForDedup
  );

  // 2. Simulate SplitInBatches per-item loop in execution-local memory
  const loopProcessedItems = [];
  const parseAiFn = new Function('$json', '$', parseAiCode);

  for (const loopItem of dedupedItems) {
    if (loopItem.json._empty_batch) {
      loopProcessedItems.push(loopItem);
      continue;
    }
    const aiMock = aiResponsesByPlaceId[loopItem.json.placeId] || {};
    const $lookupForParse = (nodeName) => {
      if (nodeName === 'Loop Over Items') {
        return { item: loopItem };
      }
      if (nodeName === 'Basic LLM Chain') {
        return { item: { json: { text: aiMock.stage1Text || null } } };
      }
      throw new Error('Unexpected node lookup: ' + nodeName);
    };
    const stage2Input = aiMock.stage2Text
      ? { content: { parts: [{ text: aiMock.stage2Text }] } }
      : { error: 'Stage 2 failed' };

    const parsedItem = parseAiFn(stage2Input, $lookupForParse);
    loopProcessedItems.push(parsedItem);
  }

  // 3. Run 'Build Batch Callback Payload' receiving only this execution's Loop Over Items done output
  const buildBatchFn = new Function('$input', '$', buildBatchCode);
  const batchOutput = buildBatchFn(
    { all: () => loopProcessedItems },
    (nodeName) => {
      if (nodeName === 'Normalize Trigger Input') {
        return { first: () => ({ json: triggerInput }) };
      }
      throw new Error('Unexpected node lookup: ' + nodeName);
    }
  );
  return batchOutput[0].json;
}

// Run Search A (search_run_id = 101, Austin) and Search B (search_run_id = 202, Seattle) concurrently
const execAPromise = Promise.resolve().then(() =>
  runExecutionSimulation(
    {
      search_run_id: 101,
      city: 'Austin',
      business_type: 'Dental Clinic',
      max_results: 5,
      callback_url: 'https://api.example.com/api/v1/integrations/n8n/callback',
      execution_id: 'exec-101'
    },
    [
      {
        title: 'Austin Smile Craft',
        placeId: 'place_austin_1',
        website: 'https://austinsmile.example.com',
        address: '100 Congress Ave',
        totalScore: 4.9,
        reviewsCount: 150
      }
    ],
    {
      place_austin_1: {
        stage1Text: JSON.stringify({
          business_summary: 'Austin dental studio.',
          confidence: 'high'
        }),
        stage2Text: JSON.stringify({
          company_name: 'Smile Craft AI Name',
          personalization_observation: 'Uses digital scanners.',
          potential_opportunity: 'Add online scheduling.',
          icebreaker: 'Saw your Austin dental studio.'
        })
      }
    }
  )
);

const execBPromise = Promise.resolve().then(() =>
  runExecutionSimulation(
    {
      search_run_id: 202,
      city: 'Seattle',
      business_type: 'HVAC',
      max_results: 5,
      callback_url: 'https://api.example.com/api/v1/integrations/n8n/callback',
      execution_id: 'exec-202'
    },
    [
      {
        title: 'Puget Sound Heating',
        placeId: 'place_seattle_1',
        website: '',
        address: '400 Broad St',
        totalScore: 4.5,
        reviewsCount: 42
      }
    ],
    {
      place_seattle_1: {
        stage1Text: null,
        stage2Text: null
      }
    }
  )
);

Promise.all([execAPromise, execBPromise]).then(([outA, outB]) => {
  console.log(JSON.stringify({ outA, outB }));
});
"""
    proc = subprocess.run(
        ["node", "-e", node_harness],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "DEDUP_JS": dedup_js,
            "PARSE_AI_JS": parse_ai_js,
            "BUILD_BATCH_JS": build_batch_js,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    sim_result = json.loads(proc.stdout.strip())
    out_a = sim_result["outA"]["payload"]
    out_b = sim_result["outB"]["payload"]

    # Prove Search A and Search B are completely isolated and never mix leads
    assert out_a["search_run_id"] == 101
    assert out_a["execution_id"] == "exec-101"
    assert len(out_a["leads"]) == 1
    assert out_a["leads"][0]["company_name"] == "Austin Smile Craft"
    assert out_a["leads"][0]["analysis"]["ai_company_name"] == "Smile Craft AI Name"
    assert out_a["leads"][0]["analysis"]["confidence"] == 0.9
    assert out_a["leads"][0]["raw_payload"]["apify"]["placeId"] == "place_austin_1"

    assert out_b["search_run_id"] == 202
    assert out_b["execution_id"] == "exec-202"
    assert len(out_b["leads"]) == 1
    assert out_b["leads"][0]["company_name"] == "Puget Sound Heating"
    assert out_b["leads"][0]["analysis"] is None
    assert out_b["leads"][0]["processing_status"] == "partial_ai_failure"
    assert out_b["leads"][0]["raw_payload"]["apify"]["placeId"] == "place_seattle_1"


def test_workflow_v2_all_11_corrections_and_paths(n8n_client: TestClient) -> None:
    """
    Verify all 11 required corrections on leadforge_discovery_qualification_v2_fastapi.json:
    1. Legacy 'On form submission' Form Trigger and connection removed; Webhook Trigger is exclusive.
    2. 'Basic LLM Chain' hasOutputParser is False.
    3. Callback URL resolution fails explicitly when missing or loopback/localhost (127.0.0.1 / localhost).
    4. search_run_id validated as positive integer (> 0).
    5. max_results validated in range 1..500 with safe default 20.
    6. 'Code in JavaScript1' parses both structured JSON and text JSON from Stage 1 and Stage 2 Gemini nodes.
    7. Failed Apify request throws an explicit error in 'Code in JavaScript' and cannot produce a successful empty callback.
    8. Controlled test mode bypasses Apify, Gemini, and Google Sheets and sends exactly 1 synthetic lead to FastAPI.
    9. 'ngrok-skip-browser-warning' header and stale 'ngrok' wording removed.
    10. Existing Apify, Gemini, and Google Sheets credentials preserved.
    11. Batch callback contract (POST /api/v1/integrations/n8n/callback, X-Callback-Secret, search_run_id, execution_id, status, leads) preserved,
        and callback failures are not masked with continueRegularOutput.
    """
    corr_raw = FASTAPI_WORKFLOW_PATH.read_text(encoding="utf-8")
    corr_json = json.loads(corr_raw)

    # Correction 9: No ngrok header or stale ngrok wording anywhere in the workflow
    assert "ngrok" not in corr_raw.lower()

    nodes = corr_json["nodes"]
    nodes_by_name = {node["name"]: node for node in nodes}
    connections = corr_json["connections"]

    # Correction 1: Legacy Form Trigger removed; Webhook Trigger is exclusive trigger
    assert "On form submission" not in nodes_by_name
    assert "On form submission" not in connections
    assert not any(n["type"] == "n8n-nodes-base.formTrigger" for n in nodes)
    webhook_node = nodes_by_name["Webhook Trigger"]
    assert webhook_node["parameters"]["httpMethod"] == "POST"
    assert webhook_node["parameters"]["path"] == "leadforge"
    assert webhook_node["parameters"]["responseMode"] == "onReceived"

    # Verify all connection source and target node references exist
    for src_name, conn_types in connections.items():
        assert src_name in nodes_by_name, f"Unknown source node in connections: {src_name}"
        for _conn_type, branches in conn_types.items():
            for branch in branches:
                for edge in branch:
                    assert edge["node"] in nodes_by_name, f"Unknown target node: {edge['node']}"

    # Verify Loop Over Items done (index 0) and loop (index 1) connections
    loop_main = connections["Loop Over Items"]["main"]
    assert len(loop_main) == 2
    assert loop_main[0][0]["node"] == "Build Batch Callback Payload"
    assert loop_main[1][0]["node"] == "IF - Has Website"
    assert connections["Restore Lead Item for Loop"]["main"][0][0]["node"] == "Loop Over Items"

    # Correction 2: Basic LLM Chain hasOutputParser is False
    assert nodes_by_name["Basic LLM Chain"]["parameters"]["hasOutputParser"] is False

    # Correction 10: Existing credentials preserved
    assert (
        nodes_by_name["HTTP Request"]["credentials"]["httpHeaderAuth"]["id"]
        == "pVNByJcoQNs66qd2"
    )
    assert (
        nodes_by_name["Google Gemini Chat Model"]["credentials"]["googlePalmApi"]["id"]
        == "Oq6R0syppCv04fWF"
    )
    assert (
        nodes_by_name["Message a model"]["credentials"]["googlePalmApi"]["id"]
        == "62ofLS03GjmYAQvi"
    )
    assert (
        nodes_by_name["Append row in sheet"]["credentials"]["googleSheetsOAuth2Api"]["id"]
        == "0j2SfKIvvTkdpBNc"
    )

    # Correction 11 & Callback failure handling: Send FastAPI Callback does NOT mask errors
    send_cb_node = nodes_by_name["Send FastAPI Callback"]
    assert send_cb_node.get("onError") is None
    assert send_cb_node.get("continueOnFail") is not True
    cb_headers = {
        h["name"]: h["value"]
        for h in send_cb_node["parameters"]["headerParameters"]["parameters"]
    }
    assert cb_headers == {
        "X-Callback-Secret": "={{ $env.N8N_CALLBACK_SECRET }}",
        "Content-Type": "application/json",
    }

    # Execute actual JS code from workflow nodes in Node.js to validate Corrections 3, 4, 5, 6, 7, 8
    normalize_js = nodes_by_name["Normalize Trigger Input"]["parameters"]["jsCode"]
    test_lead_js = nodes_by_name["Build Controlled Test Lead"]["parameters"]["jsCode"]
    dedup_js = nodes_by_name["Code in JavaScript"]["parameters"]["jsCode"]
    parse_ai_js = nodes_by_name["Code in JavaScript1"]["parameters"]["jsCode"]
    build_batch_js = nodes_by_name["Build Batch Callback Payload"]["parameters"]["jsCode"]

    validation_script = """
const assert = require('node:assert/strict');

const normalizeFn = new Function('$input', '$env', '$execution', process.env.NORMALIZE_JS);
const testLeadFn = new Function('$', process.env.TEST_LEAD_JS);
const dedupFn = new Function('$input', '$', process.env.DEDUP_JS);
const parseAiFn = new Function('$json', '$', process.env.PARSE_AI_JS);
const buildBatchFn = new Function('$input', '$', process.env.BUILD_BATCH_JS);

function callNormalize(payload, env = {}, execId = 'exec-test-99') {
  return normalizeFn(
    { first: () => ({ json: payload }) },
    env,
    { id: execId }
  )[0].json;
}

// Correction 4: search_run_id validation (rejects missing, 0, negative, float, non-numeric, boolean)
for (const badId of [undefined, null, '', 0, -5, 3.5, 'abc', true, false]) {
  assert.throws(
    () => callNormalize({
      search_run_id: badId,
      city: 'Austin',
      business_type: 'Dentist',
      callback_url: 'https://leadforge.example.com/api/v1/integrations/n8n/callback'
    }),
    /Invalid search_run_id/
  );
}

// Correction 5: max_results validation (default 20, range 1..500, rejects 0, 501, negative, float, non-numeric)
const normDefaultMax = callNormalize({
  search_run_id: 42,
  city: 'Austin',
  business_type: 'Dentist',
  callback_url: 'https://leadforge.example.com/api/v1/integrations/n8n/callback'
});
assert.equal(normDefaultMax.max_results, 20);

const normBoundaryMax = callNormalize({
  search_run_id: 42,
  city: 'Austin',
  business_type: 'Dentist',
  max_results: 500,
  callback_url: 'https://leadforge.example.com/api/v1/integrations/n8n/callback'
});
assert.equal(normBoundaryMax.max_results, 500);

for (const badMax of [0, -1, 501, 12.5, 'many', true]) {
  assert.throws(
    () => callNormalize({
      search_run_id: 42,
      city: 'Austin',
      business_type: 'Dentist',
      max_results: badMax,
      callback_url: 'https://leadforge.example.com/api/v1/integrations/n8n/callback'
    }),
    /Invalid max_results/
  );
}

// Correction 3: callback_url resolution fails explicitly when missing or loopback/localhost
assert.throws(
  () => callNormalize({ search_run_id: 10, city: 'Austin', business_type: 'Dentist' }, {}),
  /Missing required callback_url/
);
for (const loopbackUrl of [
  'http://127.0.0.1:8001/api/v1/integrations/n8n/callback',
  'http://localhost:3000/api/v1/integrations/n8n/callback',
  'https://localhost/api/v1/integrations/n8n/callback'
]) {
  assert.throws(
    () => callNormalize({
      search_run_id: 10,
      city: 'Austin',
      business_type: 'Dentist',
      callback_url: loopbackUrl
    }),
    /loopback\\/localhost/
  );
}

// Callback resolution works when callback_url is provided in the payload
const normFromPayload = callNormalize({
  search_run_id: 15,
  city: 'Miami',
  business_type: 'Med Spa',
  callback_url: 'https://tunnel.example.com/api/v1/integrations/n8n/callback'
});
assert.equal(normFromPayload.callback_url, 'https://tunnel.example.com/api/v1/integrations/n8n/callback');

// Correction 7: Failed Apify request throws explicit error in Code in JavaScript
for (const apifyErrorItem of [
  { error: { message: 'Invalid Apify token' }, statusCode: 401 },
  { errorMessage: 'Actor run failed', statusCode: 500 },
  { raw_apify: { error: 'Rate limit exceeded', statusCode: 429 } }
]) {
  assert.throws(
    () => dedupFn(
      { all: () => [{ json: apifyErrorItem }] },
      () => ({ first: () => ({ json: normDefaultMax }) })
    ),
    /Apify discovery request failed/
  );
}

// No-businesses path: empty Apify result emits _empty_batch: true -> Build Batch Callback Payload emits leads: []
const emptyDedup = dedupFn(
  { all: () => [{ json: {} }] },
  () => ({ first: () => ({ json: normDefaultMax }) })
);
assert.equal(emptyDedup.length, 1);
assert.equal(emptyDedup[0].json._empty_batch, true);

const emptyBatchPayload = buildBatchFn(
  { all: () => emptyDedup },
  () => ({ first: () => ({ json: normDefaultMax }) })
)[0].json;
assert.deepEqual(emptyBatchPayload.payload.leads, []);
assert.equal(emptyBatchPayload.payload.status, 'completed');

// Correction 8: Controlled test mode produces 1 synthetic lead -> Build Batch Callback Payload
const testTrigger = callNormalize({
  search_run_id: 77,
  city: 'TEST_ONLY_Denver',
  business_type: 'Roofing',
  callback_url: 'https://tunnel.example.com/api/v1/integrations/n8n/callback'
});
assert.equal(testTrigger.test_mode, true);
const syntheticItems = testLeadFn(() => ({ first: () => ({ json: testTrigger }) }));
assert.equal(syntheticItems.length, 1);
const testModeBatch = buildBatchFn(
  { all: () => syntheticItems },
  () => ({ first: () => ({ json: testTrigger }) })
)[0].json;

// Correction 6: Code in JavaScript1 parses both structured JSON objects and text JSON strings
const baseLoopItem = {
  json: {
    title: 'Cascade Dental Group',
    placeId: 'place_cascade_1',
    website: 'https://cascadedental.example.com',
    address: '500 Pine St',
    phone: '+1-206-555-0100',
    city: 'Seattle',
    business_type: 'Dental Clinic',
    categories: ['Dental Clinic'],
    rating: 4.8,
    reviews_count: 85,
    raw_apify: { title: 'Cascade Dental Group', placeId: 'place_cascade_1' }
  }
};

// Case 6A: Structured JSON objects from both Stage 1 and Stage 2
const parsedStructured = parseAiFn(
  {
    company_name: 'Cascade Dental Group',
    personalization_observation: 'Offers same-day crowns.',
    potential_opportunity: 'Add automated recall booking.',
    icebreaker: 'Loved your Seattle dental clinic profile.'
  },
  (nodeName) => {
    if (nodeName === 'Loop Over Items') return { item: baseLoopItem };
    if (nodeName === 'Basic LLM Chain') {
      return {
        item: {
          json: {
            output: {
              business_summary: 'Full-service Seattle dental clinic.',
              confidence: 'high'
            }
          }
        }
      };
    }
    throw new Error('Unexpected lookup: ' + nodeName);
  }
);
assert.equal(parsedStructured.json.processing_status, 'completed');
assert.equal(parsedStructured.json.analysis.business_summary, 'Full-service Seattle dental clinic.');
assert.equal(parsedStructured.json.analysis.personalization_observation, 'Offers same-day crowns.');
assert.equal(parsedStructured.json.analysis.confidence, 0.9);

// Case 6B: Markdown-fenced text JSON from both Stage 1 and Stage 2
const parsedTextJson = parseAiFn(
  {
    content: {
      parts: [
        {
          text: '```json\\n{"company_name":"Cascade Dental","personalization_observation":"Great reviews.","potential_opportunity":"Online intake forms.","icebreaker":"Hi Cascade team."}\\n```'
        }
      ]
    }
  },
  (nodeName) => {
    if (nodeName === 'Loop Over Items') return { item: baseLoopItem };
    if (nodeName === 'Basic LLM Chain') {
      return {
        item: {
          json: {
            text: '```json\\n{"business_summary":"Downtown dental practice.","confidence":"medium"}\\n```'
          }
        }
      };
    }
    throw new Error('Unexpected lookup: ' + nodeName);
  }
);
assert.equal(parsedTextJson.json.processing_status, 'completed');
assert.equal(parsedTextJson.json.analysis.business_summary, 'Downtown dental practice.');
assert.equal(parsedTextJson.json.analysis.potential_opportunity, 'Online intake forms.');
assert.equal(parsedTextJson.json.analysis.confidence, 0.6);

console.log(JSON.stringify({ testModeBatch: testModeBatch.payload }));
"""
    proc = subprocess.run(
        ["node", "-e", validation_script],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "NORMALIZE_JS": normalize_js,
            "TEST_LEAD_JS": test_lead_js,
            "DEDUP_JS": dedup_js,
            "PARSE_AI_JS": parse_ai_js,
            "BUILD_BATCH_JS": build_batch_js,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    js_out = json.loads(proc.stdout.strip())
    test_mode_payload = js_out["testModeBatch"]
    assert test_mode_payload["search_run_id"] == 77
    assert len(test_mode_payload["leads"]) == 1
    assert test_mode_payload["leads"][0]["company_name"].startswith("[TEST_ONLY]")

    # Verify the controlled test-mode batch payload is accepted by the real FastAPI callback endpoint
    run_res = n8n_client.post(
        "/api/v1/search-runs",
        json={"city": "TEST_ONLY_Denver", "business_type": "Roofing", "max_results": 10},
    )
    run_id = run_res.json()["id"]
    test_mode_payload["search_run_id"] = run_id

    cb_res = n8n_client.post(
        "/api/v1/integrations/n8n/callback",
        headers={"X-Callback-Secret": TEST_CALLBACK_SECRET},
        json=test_mode_payload,
    )
    assert cb_res.status_code == 200
    assert cb_res.json()["leads_created"] == 1


from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import create_sqlite_engine, get_db, init_db
from backend.app.main import app
from backend.app.models import (
    Lead,
    LeadAnalysis,
    SearchRun,
    SearchRunLead,
    SearchRunStatus,
)


@pytest.fixture()
def api_test_engine(tmp_path: Path) -> Engine:
    db_file = tmp_path / "test_api_leadforge.db"
    test_engine = create_sqlite_engine(f"sqlite:///{db_file}")
    init_db(test_engine)
    return test_engine


@pytest.fixture()
def api_db_session(api_test_engine: Engine) -> Generator[Session, None, None]:
    TestingSessionLocal = sessionmaker(
        bind=api_test_engine,
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
def api_client(
    api_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    from backend.app.config import get_settings

    monkeypatch.setenv("N8N_WEBHOOK_URL", "")
    get_settings.cache_clear()

    def override_get_db() -> Generator[Session, None, None]:
        yield api_db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()



def seed_sample_dataset(db: Session) -> dict[str, Any]:
    """Seed two SearchRuns, three Leads, two LeadAnalyses, and SearchRunLead links."""
    run1 = SearchRun(
        city="Austin",
        business_type="Dental Clinic",
        max_results=25,
        status=SearchRunStatus.COMPLETED.value,
    )
    run2 = SearchRun(
        city="Seattle",
        business_type="HVAC Contractor",
        max_results=15,
        status=SearchRunStatus.RUNNING.value,
    )
    db.add_all([run1, run2])
    db.commit()

    lead1 = Lead(
        company_name="Austin Bright Dental",
        website="https://austinbrightdental.example.com",
        address="101 Congress Ave, Austin, TX",
        phone="+1-512-555-0101",
        city="Austin",
        business_type="Dental Clinic",
        rating=4.9,
        reviews_count=210,
    )
    lead2 = Lead(
        company_name="Lone Star Orthodontics",
        website=None,
        address="450 Lamar Blvd, Austin, TX",
        phone="+1-512-555-0102",
        city="Austin",
        business_type="Dental Clinic",
        rating=4.2,
        reviews_count=35,
    )
    lead3 = Lead(
        company_name="Cascade Climate Control",
        website="https://cascadeclimate.example.com",
        address="800 Pike St, Seattle, WA",
        phone=None,
        city="Seattle",
        business_type="HVAC Contractor",
        rating=4.7,
        reviews_count=95,
    )
    db.add_all([lead1, lead2, lead3])
    db.commit()

    analysis1 = LeadAnalysis(
        lead_id=lead1.id,
        business_summary="High-volume cosmetic and family dental clinic in downtown Austin.",
        personalization_observation="210 reviews with a 4.9 average rating.",
        potential_opportunity="Could automate patient recall reminders.",
        icebreaker="Congrats on maintaining a 4.9 rating across 200+ Austin patients!",
        confidence=0.92,
    )
    analysis3 = LeadAnalysis(
        lead_id=lead3.id,
        business_summary="Commercial and residential HVAC specialist in Seattle.",
        personalization_observation="Strong reputation for heat pump installations.",
        potential_opportunity="No instant quote calculator on landing page.",
        icebreaker="Saw your heat pump installation work across Seattle.",
        confidence=0.85,
    )
    db.add_all([analysis1, analysis3])
    db.commit()

    # Link lead1 and lead2 to run1; link lead3 (and lead1 as cross-run) to run2
    db.add_all(
        [
            SearchRunLead(search_run_id=run1.id, lead_id=lead1.id),
            SearchRunLead(search_run_id=run1.id, lead_id=lead2.id),
            SearchRunLead(search_run_id=run2.id, lead_id=lead3.id),
            SearchRunLead(search_run_id=run2.id, lead_id=lead1.id),
        ]
    )
    db.commit()

    return {
        "run1": run1,
        "run2": run2,
        "lead1": lead1,
        "lead2": lead2,
        "lead3": lead3,
    }


def test_create_search_run_success(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/search-runs",
        json={
            "city": "  Miami  ",
            "business_type": "  Med Spa  ",
            "max_results": 30,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] > 0
    assert data["city"] == "Miami"
    assert data["business_type"] == "Med Spa"
    assert data["max_results"] == 30
    assert data["status"] == "pending"
    assert data["leads_count"] == 0
    assert data["created_at"] is not None
    assert data["completed_at"] is None


def test_create_search_run_default_max_results(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/search-runs",
        json={"city": "Boston", "business_type": "Accounting Firm"},
    )
    assert response.status_code == 201
    assert response.json()["max_results"] == 20


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},
        {"city": "", "business_type": "Plumber", "max_results": 10},
        {"city": "   ", "business_type": "Plumber", "max_results": 10},
        {"city": "Austin", "business_type": "", "max_results": 10},
        {"city": "Austin", "business_type": "   ", "max_results": 10},
        {"city": "Austin", "business_type": "Plumber", "max_results": 0},
        {"city": "Austin", "business_type": "Plumber", "max_results": -5},
        {"city": "Austin", "business_type": "Plumber", "max_results": 501},
    ],
)
def test_create_search_run_validation_errors(
    api_client: TestClient,
    invalid_payload: dict[str, Any],
) -> None:
    response = api_client.post("/api/v1/search-runs", json=invalid_payload)
    assert response.status_code == 422


def test_list_search_runs_pagination_and_filtering(
    api_client: TestClient,
    api_db_session: Session,
) -> None:
    seeded = seed_sample_dataset(api_db_session)

    # Full list
    res_all = api_client.get("/api/v1/search-runs")
    assert res_all.status_code == 200
    body_all = res_all.json()
    assert body_all["total"] == 2
    assert len(body_all["items"]) == 2
    assert all(item["leads_count"] == 2 for item in body_all["items"])

    # Pagination limit=1, offset=1
    res_page = api_client.get("/api/v1/search-runs?limit=1&offset=1")
    assert res_page.status_code == 200
    body_page = res_page.json()
    assert body_page["total"] == 2
    assert body_page["limit"] == 1
    assert body_page["offset"] == 1
    assert len(body_page["items"]) == 1

    # Filter by status
    res_status = api_client.get("/api/v1/search-runs?status=completed")
    assert res_status.status_code == 200
    items_status = res_status.json()["items"]
    assert len(items_status) == 1
    assert items_status[0]["id"] == seeded["run1"].id

    # Filter by city (case-insensitive)
    res_city = api_client.get("/api/v1/search-runs?city=seattle")
    assert res_city.status_code == 200
    items_city = res_city.json()["items"]
    assert len(items_city) == 1
    assert items_city[0]["id"] == seeded["run2"].id


def test_get_search_run_detail_and_not_found(
    api_client: TestClient,
    api_db_session: Session,
) -> None:
    seeded = seed_sample_dataset(api_db_session)
    run1_id = seeded["run1"].id

    res = api_client.get(f"/api/v1/search-runs/{run1_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == run1_id
    assert data["city"] == "Austin"
    assert data["leads_count"] == 2
    assert len(data["leads"]) == 2
    company_names = [lead["company_name"] for lead in data["leads"]]
    assert company_names == ["Austin Bright Dental", "Lone Star Orthodontics"]
    assert data["leads"][0]["analysis"]["confidence"] == 0.92
    assert data["leads"][1]["analysis"] is None

    # Missing ID returns 404
    res_404 = api_client.get("/api/v1/search-runs/99999")
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()

    # Invalid ID returns 422
    res_422 = api_client.get("/api/v1/search-runs/0")
    assert res_422.status_code == 422


def test_list_leads_pagination_and_filters(
    api_client: TestClient,
    api_db_session: Session,
) -> None:
    seeded = seed_sample_dataset(api_db_session)

    # All leads
    res_all = api_client.get("/api/v1/leads")
    assert res_all.status_code == 200
    assert res_all.json()["total"] == 3

    # Filter by city
    res_austin = api_client.get("/api/v1/leads?city=austin")
    assert res_austin.status_code == 200
    assert res_austin.json()["total"] == 2

    # Filter by business_type
    res_hvac = api_client.get("/api/v1/leads?business_type=hvac+contractor")
    assert res_hvac.status_code == 200
    assert res_hvac.json()["total"] == 1
    assert res_hvac.json()["items"][0]["company_name"] == "Cascade Climate Control"

    # Filter by search_run_id
    res_run1 = api_client.get(f"/api/v1/leads?search_run_id={seeded['run1'].id}")
    assert res_run1.status_code == 200
    assert res_run1.json()["total"] == 2

    # Filter by min_rating & min_reviews
    res_rating = api_client.get("/api/v1/leads?min_rating=4.8&min_reviews=100")
    assert res_rating.status_code == 200
    assert res_rating.json()["total"] == 1
    assert res_rating.json()["items"][0]["company_name"] == "Austin Bright Dental"

    # Filter by has_website
    res_with_site = api_client.get("/api/v1/leads?has_website=true")
    assert res_with_site.status_code == 200
    assert res_with_site.json()["total"] == 2

    res_no_site = api_client.get("/api/v1/leads?has_website=false")
    assert res_no_site.status_code == 200
    assert res_no_site.json()["total"] == 1
    assert res_no_site.json()["items"][0]["company_name"] == "Lone Star Orthodontics"

    # Filter by has_phone
    res_no_phone = api_client.get("/api/v1/leads?has_phone=false")
    assert res_no_phone.status_code == 200
    assert res_no_phone.json()["total"] == 1
    assert res_no_phone.json()["items"][0]["company_name"] == "Cascade Climate Control"

    # Filter by has_analysis
    res_analyzed = api_client.get("/api/v1/leads?has_analysis=true")
    assert res_analyzed.status_code == 200
    assert res_analyzed.json()["total"] == 2

    res_unanalyzed = api_client.get("/api/v1/leads?has_analysis=false")
    assert res_unanalyzed.status_code == 200
    assert res_unanalyzed.json()["total"] == 1

    # Filter by company_name substring query (q)
    res_q = api_client.get("/api/v1/leads?q=orthodontics")
    assert res_q.status_code == 200
    assert res_q.json()["total"] == 1
    assert res_q.json()["items"][0]["id"] == seeded["lead2"].id

    # Invalid rating range (min_rating > max_rating) returns 422
    res_bad_range = api_client.get("/api/v1/leads?min_rating=4.8&max_rating=3.5")
    assert res_bad_range.status_code == 422


def test_get_lead_detail_and_not_found(
    api_client: TestClient,
    api_db_session: Session,
) -> None:
    seeded = seed_sample_dataset(api_db_session)
    lead1_id = seeded["lead1"].id

    res = api_client.get(f"/api/v1/leads/{lead1_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == lead1_id
    assert data["company_name"] == "Austin Bright Dental"
    assert data["analysis"] is not None
    assert data["analysis"]["confidence"] == 0.92
    assert len(data["search_runs"]) == 2
    assert {sr["city"] for sr in data["search_runs"]} == {"Austin", "Seattle"}

    # Missing lead returns 404
    res_404 = api_client.get("/api/v1/leads/99999")
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()

    # Invalid lead ID returns 422
    res_422 = api_client.get("/api/v1/leads/-3")
    assert res_422.status_code == 422


def test_endpoints_avoid_n_plus_one_queries(
    api_client: TestClient,
    api_db_session: Session,
    api_test_engine: Engine,
) -> None:
    """Verify query counts stay constant (<= 3 SELECTs) even with 15 leads & analyses."""
    run = SearchRun(city="Chicago", business_type="Roofing", max_results=50)
    api_db_session.add(run)
    api_db_session.commit()

    for idx in range(15):
        lead = Lead(
            company_name=f"Chicago Roofing Co #{idx + 1}",
            website=f"https://roofer{idx + 1}.example.com",
            city="Chicago",
            business_type="Roofing",
            rating=4.5,
            reviews_count=20 + idx,
        )
        api_db_session.add(lead)
        api_db_session.flush()
        api_db_session.add(
            LeadAnalysis(
                lead_id=lead.id,
                business_summary=f"Summary #{idx + 1}",
                confidence=0.8,
            )
        )
        api_db_session.add(SearchRunLead(search_run_id=run.id, lead_id=lead.id))
    api_db_session.commit()
    run_id = run.id
    api_db_session.expire_all()

    select_statements: list[str] = []

    def count_select_queries(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: Any,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(api_test_engine, "before_cursor_execute", count_select_queries)
    try:
        # 1. GET /api/v1/leads (15 leads + 15 analyses)
        select_statements.clear()
        res_leads = api_client.get("/api/v1/leads?limit=20")
        assert res_leads.status_code == 200
        assert len(res_leads.json()["items"]) == 15
        assert len(select_statements) <= 3

        # 2. GET /api/v1/search-runs/{id} (1 run + 15 leads + 15 analyses)
        api_db_session.expire_all()
        select_statements.clear()
        res_run_detail = api_client.get(f"/api/v1/search-runs/{run_id}")
        assert res_run_detail.status_code == 200
        assert len(res_run_detail.json()["leads"]) == 15
        assert len(select_statements) <= 3
    finally:
        event.remove(api_test_engine, "before_cursor_execute", count_select_queries)

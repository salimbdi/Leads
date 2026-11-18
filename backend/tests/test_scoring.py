from collections.abc import Generator
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import create_sqlite_engine, get_db, init_db
from backend.app.main import app
from backend.app.models import Lead, LeadAnalysis, SearchRun, SearchRunLead
from backend.app.services.scoring_service import (
    DIMENSION_MAX_POINTS,
    HIGH_PRIORITY_THRESHOLD,
    MEDIUM_PRIORITY_THRESHOLD,
    calculate_lead_score,
    map_score_to_priority,
)


@pytest.fixture()
def scoring_engine(tmp_path: Path) -> Engine:
    db_file = tmp_path / "test_scoring_leadforge.db"
    eng = create_sqlite_engine(f"sqlite:///{db_file}")
    init_db(eng)
    return eng


@pytest.fixture()
def scoring_session(scoring_engine: Engine) -> Generator[Session, None, None]:
    TestingSessionLocal = sessionmaker(
        bind=scoring_engine,
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
def scoring_client(
    scoring_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    from backend.app.config import get_settings

    monkeypatch.setenv("N8N_WEBHOOK_URL", "")
    get_settings.cache_clear()

    def override_get_db() -> Generator[Session, None, None]:
        yield scoring_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_dimension_weights_total_100_and_score_boundaries() -> None:
    """Verify the 5 scoring dimension maximums sum to exactly 100 and scores stay in [0, 100]."""
    assert sum(DIMENSION_MAX_POINTS.values()) == 100
    assert set(DIMENSION_MAX_POINTS.keys()) == {
        "service_opportunity",
        "digital_presence_opportunity",
        "business_relevance",
        "contactability",
        "business_evidence",
    }

    # Minimum information case
    empty_res = calculate_lead_score()
    assert 0 <= empty_res["total_score"] <= 100
    assert empty_res["max_score"] == 100

    # Maximum opportunity case
    max_res = calculate_lead_score(
        company_name="Austin Premier Dental & Orthodontics",
        website=None,
        address="200 Congress Ave, Austin, TX 78701",
        phone="+1-512-555-0199",
        city="Austin",
        business_type="Dental Clinic",
        rating=4.2,
        reviews_count=85,
        raw_payload={"apify": {"placeId": "ChIJ_austin_001", "categories": ["Dental clinic"]}},
        business_summary="Established dental clinic operating without a dedicated web domain.",
        personalization_observation="Uses manual phone booking and lacks an online patient portal.",
        potential_opportunity=(
            "Build a custom website with online appointment scheduling, CRM patient recall system, "
            "and an AI receptionist workflow automation."
        ),
        icebreaker="Noticed your Austin dental practice has 85 reviews but no online booking site.",
        confidence=0.9,
    )
    assert max_res["total_score"] == 100
    assert max_res["priority"] == "High"
    for dim_key, max_pts in DIMENSION_MAX_POINTS.items():
        dim = max_res["dimensions"][dim_key]
        assert dim["score"] == max_pts
        assert dim["max_score"] == max_pts
        assert len(dim["reasons"]) >= 1


def test_each_scoring_dimension_independently() -> None:
    """Verify each of the 5 scoring dimensions responds deterministically to its target signals."""
    # 1. Digital Presence Opportunity: No website (25) > Social link (22) > HTTP site (19) > Clean HTTPS site (10)
    no_site = calculate_lead_score(website=None)["dimensions"]["digital_presence_opportunity"]["score"]
    social_site = calculate_lead_score(website="https://www.facebook.com/austindental")[
        "dimensions"
    ]["digital_presence_opportunity"]["score"]
    http_site = calculate_lead_score(website="http://austindental.example.com")["dimensions"][
        "digital_presence_opportunity"
    ]["score"]
    https_site = calculate_lead_score(website="https://austindental.example.com")["dimensions"][
        "digital_presence_opportunity"
    ]["score"]
    assert no_site == 25
    assert social_site == 22
    assert http_site == 19
    assert https_site == 10
    assert no_site > social_site > http_site > https_site

    # 2. Service Opportunity: Multi-pillar opportunity > baseline
    multi_service = calculate_lead_score(
        website="https://clinic.example.com",
        business_type="Dental Clinic",
        potential_opportunity=(
            "Website redesign needed, plus online booking portal, CRM recall management system, "
            "and AI chatbot workflow automation."
        ),
    )["dimensions"]["service_opportunity"]["score"]
    generic_service = calculate_lead_score(
        website="https://clinic.example.com",
        business_type="Retail",
        business_summary="Local shop selling general goods.",
    )["dimensions"]["service_opportunity"]["score"]
    assert multi_service == 30
    assert multi_service > generic_service

    # 3. Business Relevance: Tier 1 (20) > Tier 2 (15) > General (12) > Institutional (4)
    tier1 = calculate_lead_score(business_type="HVAC Contractor")["dimensions"][
        "business_relevance"
    ]["score"]
    tier2 = calculate_lead_score(business_type="Coffee Roaster & Bakery")["dimensions"][
        "business_relevance"
    ]["score"]
    tier3 = calculate_lead_score(business_type="Specialty Distributor")["dimensions"][
        "business_relevance"
    ]["score"]
    tier4 = calculate_lead_score(business_type="Municipal Government Post Office")["dimensions"][
        "business_relevance"
    ]["score"]
    assert tier1 == 20
    assert tier2 == 15
    assert tier3 == 12
    assert tier4 == 4

    # 4. Contactability: Full contact (15) > Phone + City (10) > City only (2)
    full_contact = calculate_lead_score(
        phone="+1-512-555-0199",
        address="100 Main St, Austin, TX",
        city="Austin",
        website="https://example.com",
    )["dimensions"]["contactability"]["score"]
    phone_city_only = calculate_lead_score(
        phone="+1-512-555-0199",
        address=None,
        city="Austin",
        website=None,
    )["dimensions"]["contactability"]["score"]
    city_only = calculate_lead_score(
        phone=None,
        address=None,
        city="Austin",
        website=None,
    )["dimensions"]["contactability"]["score"]
    assert full_contact == 15
    assert phone_city_only == 10
    assert city_only == 2

    # 5. Business Evidence: Capped at 10 points; high review count alone never inflates total score
    high_reviews_low_fit = calculate_lead_score(
        company_name="City Post Office",
        website="https://usps.example.com",
        business_type="Government Post Office",
        rating=5.0,
        reviews_count=5000,
    )
    assert high_reviews_low_fit["dimensions"]["business_evidence"]["score"] <= 10
    assert high_reviews_low_fit["total_score"] < MEDIUM_PRIORITY_THRESHOLD
    assert high_reviews_low_fit["priority"] == "Low"


def test_missing_website_and_missing_analysis_do_not_penalize_good_prospect() -> None:
    """
    Verify that a relevant local business missing a website and missing AI analysis
    is recognized as a strong website/service prospect rather than treated as a bad lead.
    """
    result = calculate_lead_score(
        company_name="Lone Star Plumbing & Heating",
        website=None,
        address="450 Congress Ave, Austin, TX",
        phone="+1-512-555-0144",
        city="Austin",
        business_type="Plumbing Contractor",
        rating=None,
        reviews_count=None,
    )
    # Digital presence (25) + Service baseline for unanalyzed no-website Tier 1 (20)
    # + Business relevance (20) + Contactability (14) + Evidence baseline (4) = 83 (High)
    assert result["dimensions"]["digital_presence_opportunity"]["score"] == 25
    assert result["dimensions"]["service_opportunity"]["score"] == 20
    assert result["dimensions"]["business_relevance"]["score"] == 20
    assert result["total_score"] >= HIGH_PRIORITY_THRESHOLD
    assert result["priority"] == "High"


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {
            "company_name": None,
            "website": "http://[::invalid-ipv6-url",
            "address": 12345,
            "phone": False,
            "city": None,
            "business_type": ["invalid", "list"],
            "rating": -99.5,
            "reviews_count": -400,
            "raw_payload": "{not-valid-json",
            "confidence": "not-a-float",
        },
        {
            "company_name": "   ",
            "website": "   ",
            "rating": float("nan"),
            "reviews_count": "not_an_int",
            "raw_payload": 9999,
            "potential_opportunity": True,
        },
    ],
)
def test_invalid_or_inconsistent_inputs_never_crash_scoring(bad_kwargs: dict) -> None:
    """Verify malformed, out-of-range, or unexpected types never raise exceptions."""
    res = calculate_lead_score(**bad_kwargs)
    assert isinstance(res["total_score"], int)
    assert 0 <= res["total_score"] <= 100
    assert res["priority"] in {"High", "Medium", "Low"}


def test_score_repeatability_and_priority_threshold_mapping() -> None:
    """Verify deterministic repeatability across 50 runs and exact priority boundary mapping."""
    sample_inputs = {
        "company_name": "Cascade Climate Control",
        "website": "https://cascadeclimate.example.com",
        "address": "800 Pike St, Seattle, WA",
        "phone": "+1-206-555-0199",
        "city": "Seattle",
        "business_type": "HVAC Contractor",
        "rating": 4.7,
        "reviews_count": 95,
        "potential_opportunity": "No instant quote calculator or online booking on landing page.",
    }
    first = calculate_lead_score(**sample_inputs)
    for _ in range(50):
        assert calculate_lead_score(**sample_inputs) == first

    assert map_score_to_priority(0) == "Low"
    assert map_score_to_priority(MEDIUM_PRIORITY_THRESHOLD - 1) == "Low"
    assert map_score_to_priority(MEDIUM_PRIORITY_THRESHOLD) == "Medium"
    assert map_score_to_priority(HIGH_PRIORITY_THRESHOLD - 1) == "Medium"
    assert map_score_to_priority(HIGH_PRIORITY_THRESHOLD) == "High"
    assert map_score_to_priority(100) == "High"


def test_existing_pre_phase6_sqlite_database_additive_migration_and_backfill(
    tmp_path: Path,
) -> None:
    """
    Create a legacy pre-Phase-6 SQLite database file (without score, priority, or score_breakdown),
    populate it with existing SearchRun, Lead, and LeadAnalysis records, then run init_db()
    and verify:
    1. All existing records and AI analyses are preserved intact.
    2. New columns (`score`, `priority`, `score_breakdown`) are added safely.
    3. Pre-existing leads are automatically backfilled with deterministic scores & breakdowns.
    """
    legacy_db_file = tmp_path / "legacy_pre_phase6.db"
    conn = sqlite3.connect(str(legacy_db_file))
    cursor = conn.cursor()

    cursor.executescript(
        """
        CREATE TABLE search_runs (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            city VARCHAR(120) NOT NULL,
            business_type VARCHAR(120) NOT NULL,
            max_results INTEGER NOT NULL DEFAULT 20,
            status VARCHAR(32) NOT NULL DEFAULT 'completed',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME
        );
        CREATE TABLE leads (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            company_name VARCHAR(255) NOT NULL,
            website VARCHAR(512),
            address VARCHAR(512),
            phone VARCHAR(64),
            city VARCHAR(120) NOT NULL,
            business_type VARCHAR(120) NOT NULL,
            rating FLOAT,
            reviews_count INTEGER,
            raw_payload TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE lead_analyses (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
            business_summary TEXT,
            personalization_observation TEXT,
            potential_opportunity TEXT,
            icebreaker TEXT,
            confidence FLOAT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE search_run_leads (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
            lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (search_run_id, lead_id)
        );

        INSERT INTO search_runs (id, city, business_type, max_results, status)
        VALUES (1, 'Austin', 'Dental Clinic', 10, 'completed');

        INSERT INTO leads (id, company_name, website, address, phone, city, business_type, rating, reviews_count)
        VALUES
            (1, 'Legacy Austin Dental (No Website)', NULL, '100 Congress Ave, Austin, TX', '+1-512-555-0100', 'Austin', 'Dental Clinic', 4.4, 62),
            (2, 'Legacy Metro Law Group', 'https://metrolaw.example.com', '200 Lavaca St, Austin, TX', '+1-512-555-0200', 'Austin', 'Law Firm', 4.8, 120);

        INSERT INTO lead_analyses (lead_id, business_summary, personalization_observation, potential_opportunity, icebreaker, confidence)
        VALUES
            (2, 'Corporate and family law firm in Austin.', 'Uses static contact page.', 'Add client intake portal and automated appointment scheduling.', 'Saw your Austin legal team.', 0.85);

        INSERT INTO search_run_leads (search_run_id, lead_id) VALUES (1, 1), (1, 2);
        """
    )
    conn.commit()
    conn.close()

    # Now open the legacy DB with SQLAlchemy and run init_db()
    legacy_engine = create_sqlite_engine(f"sqlite:///{legacy_db_file}")
    init_db(legacy_engine)

    inspector = inspect(legacy_engine)
    cols = {col["name"] for col in inspector.get_columns("leads")}
    assert {"score", "priority", "score_breakdown"}.issubset(cols)

    with Session(bind=legacy_engine) as session:
        leads = session.scalars(select(Lead).order_by(Lead.id.asc())).all()
        assert len(leads) == 2

        lead1, lead2 = leads[0], leads[1]
        assert lead1.company_name == "Legacy Austin Dental (No Website)"
        assert lead1.score >= HIGH_PRIORITY_THRESHOLD
        assert lead1.priority == "High"
        assert lead1.score_breakdown is not None
        bd1 = json.loads(lead1.score_breakdown)
        assert bd1["total_score"] == lead1.score
        assert bd1["dimensions"]["digital_presence_opportunity"]["score"] == 25

        assert lead2.company_name == "Legacy Metro Law Group"
        assert lead2.analysis is not None
        assert lead2.analysis.confidence == 0.85
        assert lead2.score >= HIGH_PRIORITY_THRESHOLD
        assert lead2.priority == "High"
        bd2 = json.loads(lead2.score_breakdown)
        assert bd2["total_score"] == lead2.score

    legacy_engine.dispose()


def test_leads_api_exposes_score_breakdown_and_supports_sorting_and_filtering(
    scoring_client: TestClient,
    scoring_session: Session,
) -> None:
    """Verify GET /api/v1/leads and GET /api/v1/leads/{id} expose score, priority, score_breakdown, sorting, and filtering."""
    run = SearchRun(city="Austin", business_type="Dental Clinic", max_results=20, status="completed")
    scoring_session.add(run)
    scoring_session.commit()

    # High-score lead (no website + Tier 1 category + full contact + AI opportunity)
    lead_high = Lead(
        company_name="High Potential Dental Studio",
        website=None,
        address="100 Congress Ave, Austin, TX",
        phone="+1-512-555-0111",
        city="Austin",
        business_type="Dental Clinic",
        rating=4.3,
        reviews_count=48,
    )
    # Medium-score lead (existing HTTPS website, no phone/address, moderate category)
    lead_med = Lead(
        company_name="Midtown Coffee Roasters",
        website="https://midtowncoffee.example.com",
        address=None,
        phone=None,
        city="Austin",
        business_type="Coffee Shop",
        rating=4.6,
        reviews_count=30,
    )
    # Low-score lead (institutional low-fit category, existing HTTPS website, no phone/address)
    lead_low = Lead(
        company_name="Downtown Public Parking Lot",
        website="https://cityparking.example.com",
        address=None,
        phone=None,
        city="Austin",
        business_type="Public Parking Lot",
        rating=2.5,
        reviews_count=0,
    )
    scoring_session.add_all([lead_high, lead_med, lead_low])
    scoring_session.commit()

    analysis_high = LeadAnalysis(
        lead_id=lead_high.id,
        business_summary="Busy dental studio without a web domain.",
        potential_opportunity="Build a modern patient booking website and AI recall automation.",
        confidence=0.9,
    )
    scoring_session.add(analysis_high)
    scoring_session.add_all(
        [
            SearchRunLead(search_run_id=run.id, lead_id=lead_high.id),
            SearchRunLead(search_run_id=run.id, lead_id=lead_med.id),
            SearchRunLead(search_run_id=run.id, lead_id=lead_low.id),
        ]
    )
    scoring_session.commit()

    # 1. Sort by score DESC
    res_desc = scoring_client.get("/api/v1/leads?sort_by=score&sort_order=desc")
    assert res_desc.status_code == 200
    items_desc = res_desc.json()["items"]
    assert [i["company_name"] for i in items_desc] == [
        "High Potential Dental Studio",
        "Midtown Coffee Roasters",
        "Downtown Public Parking Lot",
    ]
    assert items_desc[0]["priority"] == "High"
    assert items_desc[1]["priority"] == "Medium"
    assert items_desc[2]["priority"] == "Low"
    assert items_desc[0]["score"] > items_desc[1]["score"] > items_desc[2]["score"]

    # 2. Sort by score ASC
    res_asc = scoring_client.get("/api/v1/leads?sort_by=score&sort_order=asc")
    assert res_asc.status_code == 200
    items_asc = res_asc.json()["items"]
    assert [i["company_name"] for i in items_asc] == [
        "Downtown Public Parking Lot",
        "Midtown Coffee Roasters",
        "High Potential Dental Studio",
    ]

    # 3. Filter by priority=High
    res_high = scoring_client.get("/api/v1/leads?priority=High")
    assert res_high.status_code == 200
    assert res_high.json()["total"] == 1
    assert res_high.json()["items"][0]["company_name"] == "High Potential Dental Studio"

    # 4. Filter by min_score and max_score
    res_range = scoring_client.get("/api/v1/leads?min_score=45&max_score=69")
    assert res_range.status_code == 200
    assert res_range.json()["total"] == 1
    assert res_range.json()["items"][0]["company_name"] == "Midtown Coffee Roasters"

    # 5. Invalid min_score > max_score or invalid priority returns 422
    assert scoring_client.get("/api/v1/leads?min_score=80&max_score=40").status_code == 422
    assert scoring_client.get("/api/v1/leads?priority=Urgent").status_code == 422

    # 6. Verify single lead detail response contains full structured breakdown
    detail_res = scoring_client.get(f"/api/v1/leads/{lead_high.id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["score"] == detail["score_breakdown"]["total_score"]
    assert detail["priority"] == detail["score_breakdown"]["priority"] == "High"
    assert set(detail["score_breakdown"]["dimensions"].keys()) == {
        "service_opportunity",
        "digital_presence_opportunity",
        "business_relevance",
        "contactability",
        "business_evidence",
    }

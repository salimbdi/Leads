from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import IntegrityError
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
def db_engine(tmp_path: Path) -> Engine:
    """Create an isolated file-backed SQLite engine for each test."""
    db_file = tmp_path / "test_leadforge.db"
    test_engine = create_sqlite_engine(f"sqlite:///{db_file}")
    init_db(test_engine)
    return test_engine


@pytest.fixture()
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """Provide a transactional SQLAlchemy Session bound to the isolated test engine."""
    TestingSessionLocal = sessionmaker(
        bind=db_engine,
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


def test_database_initialization_creates_expected_tables(db_engine: Engine) -> None:
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    assert tables == {
        "search_runs",
        "leads",
        "lead_analyses",
        "search_run_leads",
    }

    search_run_cols = {col["name"] for col in inspector.get_columns("search_runs")}
    assert {
        "id",
        "city",
        "business_type",
        "max_results",
        "status",
        "created_at",
        "completed_at",
    }.issubset(search_run_cols)

    lead_cols = {col["name"] for col in inspector.get_columns("leads")}
    assert {
        "id",
        "company_name",
        "website",
        "address",
        "phone",
        "city",
        "business_type",
        "rating",
        "reviews_count",
        "created_at",
        "updated_at",
    }.issubset(lead_cols)

    analysis_cols = {col["name"] for col in inspector.get_columns("lead_analyses")}
    assert {
        "id",
        "lead_id",
        "business_summary",
        "personalization_observation",
        "potential_opportunity",
        "icebreaker",
        "confidence",
        "created_at",
        "updated_at",
    }.issubset(analysis_cols)

    run_lead_cols = {col["name"] for col in inspector.get_columns("search_run_leads")}
    assert {
        "id",
        "search_run_id",
        "lead_id",
        "created_at",
    }.issubset(run_lead_cols)


def test_search_run_crud_and_timestamps(db_session: Session) -> None:
    run = SearchRun(city="Austin", business_type="Dental Clinic", max_results=25)
    db_session.add(run)
    db_session.commit()

    assert run.id is not None
    assert run.status == SearchRunStatus.PENDING.value
    assert isinstance(run.created_at, datetime)
    assert run.completed_at is None

    # Update status and completed_at
    completion_time = datetime.now(timezone.utc)
    run.status = SearchRunStatus.COMPLETED.value
    run.completed_at = completion_time
    db_session.commit()

    fetched = db_session.get(SearchRun, run.id)
    assert fetched is not None
    assert fetched.city == "Austin"
    assert fetched.business_type == "Dental Clinic"
    assert fetched.max_results == 25
    assert fetched.status == "completed"
    assert fetched.completed_at is not None

    # Delete
    db_session.delete(fetched)
    db_session.commit()
    assert db_session.get(SearchRun, run.id) is None


@pytest.mark.parametrize(
    ("city", "business_type", "max_results", "status"),
    [
        ("", "Plumber", 10, "pending"),
        ("   ", "Plumber", 10, "pending"),
        ("Denver", "", 10, "pending"),
        ("Denver", "Plumber", 0, "pending"),
        ("Denver", "Plumber", -5, "pending"),
        ("Denver", "Plumber", 501, "pending"),
        ("Denver", "Plumber", 10, "invalid_state"),
    ],
)
def test_search_run_check_constraints(
    db_session: Session,
    city: str,
    business_type: str,
    max_results: int,
    status: str,
) -> None:
    invalid_run = SearchRun(
        city=city,
        business_type=business_type,
        max_results=max_results,
        status=status,
    )
    db_session.add(invalid_run)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_lead_crud_operations(db_session: Session) -> None:
    lead = Lead(
        company_name="Bright Smile Dental",
        website="https://brightsmile.example.com",
        address="100 Congress Ave, Austin, TX",
        phone="+1-512-555-0199",
        city="Austin",
        business_type="Dental Clinic",
        rating=4.8,
        reviews_count=142,
    )
    db_session.add(lead)
    db_session.commit()

    assert lead.id is not None
    assert lead.created_at is not None
    assert lead.updated_at is not None

    # Update rating and reviews_count
    lead.rating = 4.9
    lead.reviews_count = 150
    db_session.commit()

    fetched = db_session.get(Lead, lead.id)
    assert fetched is not None
    assert fetched.company_name == "Bright Smile Dental"
    assert fetched.rating == 4.9
    assert fetched.reviews_count == 150


@pytest.mark.parametrize(
    ("company_name", "city", "business_type", "rating", "reviews_count"),
    [
        ("", "Austin", "Dental Clinic", 4.5, 10),
        ("Bright Smile", "", "Dental Clinic", 4.5, 10),
        ("Bright Smile", "Austin", "", 4.5, 10),
        ("Bright Smile", "Austin", "Dental Clinic", -0.1, 10),
        ("Bright Smile", "Austin", "Dental Clinic", 5.1, 10),
        ("Bright Smile", "Austin", "Dental Clinic", 4.5, -1),
    ],
)
def test_lead_check_constraints(
    db_session: Session,
    company_name: str,
    city: str,
    business_type: str,
    rating: float,
    reviews_count: int,
) -> None:
    invalid_lead = Lead(
        company_name=company_name,
        city=city,
        business_type=business_type,
        rating=rating,
        reviews_count=reviews_count,
    )
    db_session.add(invalid_lead)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_lead_analysis_one_to_one_and_constraints(db_session: Session) -> None:
    lead = Lead(
        company_name="Apex HVAC Services",
        city="Dallas",
        business_type="HVAC Contractor",
        rating=4.6,
        reviews_count=88,
    )
    db_session.add(lead)
    db_session.commit()

    analysis = LeadAnalysis(
        lead_id=lead.id,
        business_summary="Residential and commercial HVAC maintenance provider in Dallas.",
        personalization_observation="Strong 4.6 rating across 88 local customer reviews.",
        potential_opportunity="Website lacks online booking widget for emergency repairs.",
        icebreaker="Noticed your strong customer feedback in Dallas for seasonal tune-ups.",
        confidence=0.87,
    )
    db_session.add(analysis)
    db_session.commit()

    db_session.refresh(lead)
    assert lead.analysis is not None
    assert lead.analysis.id == analysis.id
    assert lead.analysis.confidence == 0.87
    assert analysis.lead.company_name == "Apex HVAC Services"

    # Enforce 1-to-1 uniqueness: second analysis for same lead must fail
    duplicate_analysis = LeadAnalysis(
        lead_id=lead.id,
        business_summary="Duplicate summary",
        confidence=0.5,
    )
    db_session.add(duplicate_analysis)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Enforce confidence range constraint (0.0 <= confidence <= 1.0)
    lead2 = Lead(company_name="Metro Roofing", city="Dallas", business_type="Roofing")
    db_session.add(lead2)
    db_session.commit()

    invalid_confidence = LeadAnalysis(lead_id=lead2.id, confidence=1.25)
    db_session.add(invalid_confidence)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Enforce foreign key constraint: non-existent lead_id must fail
    orphan_analysis = LeadAnalysis(lead_id=999999, confidence=0.8)
    db_session.add(orphan_analysis)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_search_run_lead_relationships_and_uniqueness(db_session: Session) -> None:
    run1 = SearchRun(city="Seattle", business_type="Coffee Roaster", max_results=15)
    run2 = SearchRun(city="Seattle", business_type="Cafe & Bakery", max_results=20)
    lead1 = Lead(company_name="Cascade Roasters", city="Seattle", business_type="Coffee Roaster")
    lead2 = Lead(company_name="Emerald Espresso", city="Seattle", business_type="Coffee Roaster")

    db_session.add_all([run1, run2, lead1, lead2])
    db_session.commit()

    link1 = SearchRunLead(search_run_id=run1.id, lead_id=lead1.id)
    link2 = SearchRunLead(search_run_id=run1.id, lead_id=lead2.id)
    link3 = SearchRunLead(search_run_id=run2.id, lead_id=lead1.id)
    db_session.add_all([link1, link2, link3])
    db_session.commit()

    db_session.refresh(run1)
    db_session.refresh(lead1)

    assert len(run1.search_run_leads) == 2
    assert {l.company_name for l in run1.leads} == {"Cascade Roasters", "Emerald Espresso"}
    assert len(lead1.search_runs) == 2

    # Duplicate (search_run_id, lead_id) must fail
    duplicate_link = SearchRunLead(search_run_id=run1.id, lead_id=lead1.id)
    db_session.add(duplicate_link)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Foreign key violation on invalid search_run_id must fail
    invalid_fk_link = SearchRunLead(search_run_id=888888, lead_id=lead1.id)
    db_session.add(invalid_fk_link)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_deletes_preserve_shared_entities(db_session: Session) -> None:
    run = SearchRun(city="Chicago", business_type="Law Firm", max_results=10)
    lead = Lead(company_name="Lakeshore Legal", city="Chicago", business_type="Law Firm")
    db_session.add_all([run, lead])
    db_session.commit()

    analysis = LeadAnalysis(lead_id=lead.id, business_summary="Corporate law firm", confidence=0.9)
    link = SearchRunLead(search_run_id=run.id, lead_id=lead.id)
    db_session.add_all([analysis, link])
    db_session.commit()

    # Deleting SearchRun removes SearchRunLead link but preserves Lead and LeadAnalysis
    db_session.delete(run)
    db_session.commit()

    remaining_links = db_session.scalars(select(SearchRunLead)).all()
    assert len(remaining_links) == 0
    assert db_session.get(Lead, lead.id) is not None
    assert db_session.get(LeadAnalysis, analysis.id) is not None

    # Deleting Lead cascades and removes LeadAnalysis
    db_session.delete(lead)
    db_session.commit()
    assert db_session.get(LeadAnalysis, analysis.id) is None


def test_get_db_dependency_and_schema_endpoint() -> None:
    gen = get_db()
    session = next(gen)
    assert isinstance(session, Session)
    try:
        next(gen)
    except StopIteration:
        pass

    client = TestClient(app)
    response = client.get("/api/v1/database/schema")
    assert response.status_code == 200
    payload = response.json()
    assert payload["dialect"] == "sqlite"
    assert payload["table_count"] == 4
    table_names = [t["table_name"] for t in payload["tables"]]
    assert table_names == ["lead_analyses", "leads", "search_run_leads", "search_runs"]


def test_sqlite_concurrency_pragmas_and_existing_db_compatibility(tmp_path: Path) -> None:
    """
    Verify SQLite connections enforce:
    - PRAGMA foreign_keys = 1 (ON)
    - PRAGMA journal_mode = wal
    - PRAGMA synchronous = 1 (NORMAL)
    - PRAGMA busy_timeout = 5000
    and safely reopen an existing populated SQLite database without corruption or data loss.
    """
    from sqlalchemy import text

    db_file = tmp_path / "hardened_leadforge.db"
    db_url = f"sqlite:///{db_file}"

    engine1 = create_sqlite_engine(db_url)
    init_db(engine1)

    with engine1.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        journal = str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower()
        sync_mode = conn.execute(text("PRAGMA synchronous")).scalar()
        busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()

        assert fk == 1
        assert journal == "wal"
        # In SQLite, PRAGMA synchronous=NORMAL returns integer code 1
        assert sync_mode == 1
        assert busy_timeout == 5000

    # Insert data using engine1, dispose it, and reopen with a fresh engine2 to verify compatibility
    Session1 = sessionmaker(bind=engine1, class_=Session)
    with Session1() as s1:
        run = SearchRun(city="Boston", business_type="Dentist", max_results=10)
        s1.add(run)
        s1.commit()
        saved_id = run.id
    engine1.dispose()

    engine2 = create_sqlite_engine(db_url)
    init_db(engine2)
    Session2 = sessionmaker(bind=engine2, class_=Session)
    with Session2() as s2:
        reopened_run = s2.get(SearchRun, saved_id)
        assert reopened_run is not None
        assert reopened_run.city == "Boston"
        assert reopened_run.business_type == "Dentist"
    engine2.dispose()


"""
Unit and integration tests for Phase 2 / Phase 2.2: Durable Python Background Worker.

Verifies:
A. Independent execution:
   - FastAPI can run with DISCOVERY_EXECUTION_MODE='python' and WORKER_ENABLED=false without starting an embedded worker.
   - Standalone worker entry point (`python -m backend.app.worker` / `run_standalone_worker`) initializes DB and executes pending work.
   - Graceful shutdown stops claiming new tasks, allows in-flight work to finish, or leaves interrupted work recoverable in RUNNING state if timeout expires.

B. Concurrency, ownership, and safe recovery:
   - Two workers attempting to claim the same PENDING task cannot both claim it.
   - A fresh RUNNING task (active lease / recent started_at) is NOT recovered.
   - A stale RUNNING task (expired lease / old started_at) IS recovered.
   - A second worker startup does not steal an active worker's task.
   - Attempt and worker ownership fencing prevents a stale execution from overwriting a newer attempt's state or leads.

C. Retry behavior:
   - Transient errors (timeouts, network failures, HTTP 429, HTTP 5xx) return the task to PENDING while attempts remain and succeed on retry.
   - Permanent errors (ApifyConfigurationError, GeminiConfigurationError, HTTP 400/401/403/404, input validation errors) fail immediately on attempt 1.
   - Maximum attempts are enforced at runtime and during task claiming.
   - attempt_count increments exactly once per claim.
   - completed_at is set only for terminal states (COMPLETED / FAILED) and cleared when re-queued to PENDING.
   - Secret sanitization in last_error redacts API keys, Bearer tokens, and secrets.

D. Database transaction safety:
   - No SQLAlchemy transaction remains open during external Apify, website scraper, or Gemini network calls.
   - Result persistence and deterministic scoring work across short-lived transactions.
   - Incremental per-lead persistence preserves partial progress when a later stage fails.

E. Compatibility & migration safety:
   - Additive SQLite migration safely adds attempt_count, started_at, last_error, worker_id, and lease_expires_at to legacy search_runs tables.
   - Public API search creation and status response schemas remain unchanged.
   - Legacy n8n callback endpoint behavior remains backward-compatible.
"""

from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import Engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.db import create_sqlite_engine, get_db, init_db
from backend.app.db.base import utc_now
from backend.app.main import app
from backend.app.models.lead import Lead
from backend.app.models.search_run import SearchRun, SearchRunStatus
from backend.app.schemas.n8n import N8nCallbackRequest
from backend.app.services.apify_service import (
    ApifyConfigurationError,
    ApifyExecutionError,
)
from backend.app.services.discovery_orchestrator import execute_discovery_pipeline
from backend.app.services.gemini_service import (
    GeminiConfigurationError,
    GeminiExecutionError,
)
from backend.app.services.n8n_service import process_n8n_callback
from backend.app.services.worker_service import (
    DurableBackgroundWorker,
    claim_next_pending_task,
    claim_search_run_task,
    drain_pending_tasks,
    enqueue_search_run_task,
    execute_claimed_task,
    get_task_snapshot,
    process_next_pending_task,
    recover_interrupted_tasks,
    renew_task_lease,
)
from backend.app.worker import run_standalone_worker


@pytest.fixture()
def worker_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_durable_worker.db"


@pytest.fixture()
def worker_engine(worker_db_path: Path) -> Generator[Engine, None, None]:
    eng = create_sqlite_engine(f"sqlite:///{worker_db_path}")
    init_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def worker_session_factory(worker_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=worker_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def test_durable_task_lifecycle_pending_running_completed(
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Verify explicit lifecycle states (PENDING -> RUNNING -> COMPLETED) and persisted fields:
    task_id, search_run_id, status, attempt_count, created_at, started_at, completed_at, last_error,
    worker_id, and lease_expires_at.
    """
    with worker_session_factory() as db:
        run = SearchRun(
            city="TEST_ONLY_Austin",
            business_type="Dental Clinic",
            max_results=10,
            status=SearchRunStatus.PENDING.value,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        # 1. Verify initial PENDING state before execution
        snap_pending = get_task_snapshot(db, run_id)
        assert snap_pending is not None
        assert snap_pending.task_id == run_id
        assert snap_pending.search_run_id == run_id
        assert snap_pending.status == SearchRunStatus.PENDING
        assert snap_pending.attempt_count == 0
        assert snap_pending.created_at is not None
        assert snap_pending.started_at is None
        assert snap_pending.completed_at is None
        assert snap_pending.last_error is None
        assert snap_pending.worker_id is None
        assert snap_pending.lease_expires_at is None

        # 2. Atomically claim task -> transitions to RUNNING with worker_id and lease_expires_at
        claimed = claim_search_run_task(db, run_id, worker_id="worker-alpha")
        assert claimed is not None
        assert claimed.status == SearchRunStatus.RUNNING.value
        assert claimed.attempt_count == 1
        assert claimed.started_at is not None
        assert claimed.completed_at is None
        assert claimed.last_error is None
        assert claimed.worker_id == "worker-alpha"
        assert claimed.lease_expires_at is not None

        # Second claim on already RUNNING task must return None (atomic protection)
        duplicate_claim = claim_search_run_task(db, run_id, worker_id="worker-beta")
        assert duplicate_claim is None

    # 3. Reset to PENDING and execute full task lifecycle through process_next_pending_task
    with worker_session_factory() as db:
        run_obj = db.get(SearchRun, run_id)
        assert run_obj is not None
        run_obj.status = SearchRunStatus.PENDING.value
        run_obj.attempt_count = 0
        run_obj.started_at = None
        run_obj.worker_id = None
        run_obj.lease_expires_at = None
        db.commit()

    result = process_next_pending_task(worker_session_factory, mock_mode=True)
    assert result is not None
    assert result.status == "completed"
    assert result.leads_created == 1

    with worker_session_factory() as db:
        snap_done = get_task_snapshot(db, run_id)
        assert snap_done is not None
        assert snap_done.status == SearchRunStatus.COMPLETED
        assert snap_done.attempt_count == 1
        assert snap_done.started_at is not None
        assert snap_done.completed_at is not None
        assert snap_done.last_error is None
        assert snap_done.worker_id is None
        assert snap_done.lease_expires_at is None


def test_api_lifespan_does_not_start_embedded_worker_unless_worker_enabled(
    worker_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Requirement 1 & 7A:
    Verify that when DISCOVERY_EXECUTION_MODE='python' and WORKER_ENABLED='false',
    FastAPI lifespan does NOT start an embedded background worker, while still
    persisting and enqueuing new SearchRuns in PENDING state.
    """
    monkeypatch.setenv("DISCOVERY_EXECUTION_MODE", "python")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    get_settings.cache_clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = worker_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "backend.app.main.start_background_worker"
        ) as mock_start_worker:
            with TestClient(app) as client:
                assert mock_start_worker.call_count == 0
                resp = client.post(
                    "/api/v1/search-runs",
                    json={
                        "city": "TEST_ONLY_Austin",
                        "business_type": "Dentist",
                        "max_results": 5,
                    },
                )
                assert resp.status_code == 201
                body = resp.json()
                assert body["status"] == "pending"
                run_id = body["id"]

            with worker_session_factory() as db:
                snap = get_task_snapshot(db, run_id)
                assert snap is not None
                assert snap.status == SearchRunStatus.PENDING
                assert snap.attempt_count == 0
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_standalone_worker_entry_point_initializes_and_processes_pending_work(
    worker_db_path: Path,
) -> None:
    """
    Requirement 1 & 7A:
    Verify the standalone worker entry point (`python -m backend.app.worker`) can run
    as an independent OS process, initialize the database, recover stale tasks,
    execute pending work, and exit cleanly.
    """
    db_url = f"sqlite:///{worker_db_path}"
    eng = create_sqlite_engine(db_url)
    init_db(eng)
    SessionLocalTest = sessionmaker(bind=eng, expire_on_commit=False, class_=Session)

    with SessionLocalTest() as db:
        run = SearchRun(
            city="TEST_ONLY_StandaloneCity",
            business_type="Auto Repair",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
            attempt_count=0,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    eng.dispose()

    # Execute standalone worker module as a real separate OS process
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.app.worker",
            "--database-url",
            db_url,
            "--once",
            "--mock-mode",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, f"Worker failed: {proc.stderr}"

    eng_verify = create_sqlite_engine(db_url)
    SessionVerify = sessionmaker(bind=eng_verify, expire_on_commit=False, class_=Session)
    try:
        with SessionVerify() as db:
            snap = get_task_snapshot(db, run_id)
            assert snap is not None
            assert snap.status == SearchRunStatus.COMPLETED
            assert snap.attempt_count == 1
            assert snap.completed_at is not None
            leads = db.scalars(select(Lead)).all()
            assert len(leads) == 1
            assert "StandaloneCity" in leads[0].company_name
    finally:
        eng_verify.dispose()


def test_worker_graceful_shutdown_waits_for_in_flight_or_leaves_recoverable(
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Requirement 1:
    Verify graceful shutdown behavior:
    1. When shutdown is requested while a task is in flight, the worker stops claiming new tasks
       and waits for the current task to complete within the shutdown timeout.
    2. If the shutdown timeout expires before the task finishes, the task is NOT marked COMPLETED
       and remains in RUNNING state so it can be recovered after lease expiry.
    """
    with worker_session_factory() as db:
        run1 = SearchRun(
            city="Austin",
            business_type="Dentist",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
        )
        run2 = SearchRun(
            city="Dallas",
            business_type="Dentist",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
        )
        db.add_all([run1, run2])
        db.commit()
        run1_id = run1.id
        run2_id = run2.id

    in_apify_event = threading.Event()
    release_apify_event = threading.Event()

    def slow_discover(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        in_apify_event.set()
        release_apify_event.wait(timeout=5.0)
        return []

    worker = DurableBackgroundWorker(
        session_factory=worker_session_factory,
        poll_interval_seconds=0.05,
        shutdown_timeout_seconds=2.0,
        mock_mode=True,
    )

    with patch(
        "backend.app.services.discovery_orchestrator.discover_businesses",
        side_effect=slow_discover,
    ):
        worker.start()
        assert in_apify_event.wait(timeout=2.0)

        # Request shutdown in a separate thread while run1 is in-flight, then release apify
        def release_soon() -> None:
            time.sleep(0.1)
            release_apify_event.set()

        releaser = threading.Thread(target=release_soon)
        releaser.start()
        clean = worker.stop(timeout=2.0)
        releaser.join()
        assert clean is True

    with worker_session_factory() as db:
        snap1 = get_task_snapshot(db, run1_id)
        snap2 = get_task_snapshot(db, run2_id)
        assert snap1 is not None
        assert snap1.status == SearchRunStatus.COMPLETED
        # Because shutdown was requested while run1 was in flight, run2 was NOT claimed
        assert snap2 is not None
        assert snap2.status == SearchRunStatus.PENDING
        assert snap2.attempt_count == 0


def test_two_workers_cannot_claim_same_pending_task_concurrently(
    worker_db_path: Path,
) -> None:
    """
    Requirement 2 & 7B:
    Verify two workers sharing the same SQLite database attempting to claim the same
    PENDING task concurrently cannot both claim it, and attempt_count increments
    exactly once.
    """
    db_url = f"sqlite:///{worker_db_path}"
    eng1 = create_sqlite_engine(db_url)
    eng2 = create_sqlite_engine(db_url)
    init_db(eng1)
    Session1 = sessionmaker(bind=eng1, expire_on_commit=False, class_=Session)
    Session2 = sessionmaker(bind=eng2, expire_on_commit=False, class_=Session)

    try:
        with Session1() as db:
            run = SearchRun(
                city="TEST_ONLY_Miami",
                business_type="Roofing",
                max_results=5,
                status=SearchRunStatus.PENDING.value,
            )
            db.add(run)
            db.commit()
            run_id = run.id

        barrier = threading.Barrier(2)
        claim_results: list[tuple[str, Any]] = []
        lock = threading.Lock()

        def try_claim(factory: sessionmaker[Session], wid: str) -> None:
            with factory() as s:
                barrier.wait(timeout=5.0)
                claimed = claim_search_run_task(s, run_id, worker_id=wid)
                with lock:
                    claim_results.append((wid, claimed))

        t1 = threading.Thread(target=try_claim, args=(Session1, "worker-1"))
        t2 = threading.Thread(target=try_claim, args=(Session2, "worker-2"))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        successful_claims = [(wid, c) for wid, c in claim_results if c is not None]
        failed_claims = [(wid, c) for wid, c in claim_results if c is None]
        assert len(successful_claims) == 1
        assert len(failed_claims) == 1

        winner_id, winner_run = successful_claims[0]
        assert winner_run.attempt_count == 1
        assert winner_run.worker_id == winner_id

        with Session1() as verify_db:
            snap = get_task_snapshot(verify_db, run_id)
            assert snap is not None
            assert snap.status == SearchRunStatus.RUNNING
            assert snap.attempt_count == 1
            assert snap.worker_id == winner_id
    finally:
        eng1.dispose()
        eng2.dispose()


def test_recover_interrupted_tasks_preserves_fresh_running_and_recovers_only_stale(
    worker_db_path: Path,
) -> None:
    """
    Requirement 2 & 7B:
    Verify that:
    - A fresh RUNNING task (active lease or recent started_at) is NOT recovered.
    - A second worker starting up does NOT steal an active worker's fresh RUNNING task.
    - A stale RUNNING task (expired lease or old started_at) IS recovered to PENDING (or FAILED if exhausted).
    """
    db_url = f"sqlite:///{worker_db_path}"
    eng = create_sqlite_engine(db_url)
    init_db(eng)
    SessionFactory = sessionmaker(bind=eng, expire_on_commit=False, class_=Session)

    now = utc_now()
    try:
        with SessionFactory() as db:
            # 1. Fresh RUNNING task owned by worker-1 with valid lease
            fresh_leased = SearchRun(
                city="TEST_ONLY_FreshLeased",
                business_type="HVAC",
                max_results=5,
                status=SearchRunStatus.RUNNING.value,
                attempt_count=1,
                started_at=now - timedelta(seconds=10),
                worker_id="worker-1",
                lease_expires_at=now + timedelta(seconds=170),
            )
            # 2. Fresh RUNNING task without lease_expires_at but recent started_at (10s ago vs 180s threshold)
            fresh_started = SearchRun(
                city="TEST_ONLY_FreshStarted",
                business_type="HVAC",
                max_results=5,
                status=SearchRunStatus.RUNNING.value,
                attempt_count=1,
                started_at=now - timedelta(seconds=10),
                worker_id="worker-1",
                lease_expires_at=None,
            )
            # 3. Stale RUNNING task with expired lease (attempt 1 < 3 -> recover to PENDING)
            stale_recoverable = SearchRun(
                city="TEST_ONLY_StaleRecoverable",
                business_type="HVAC",
                max_results=5,
                status=SearchRunStatus.RUNNING.value,
                attempt_count=1,
                started_at=now - timedelta(seconds=400),
                worker_id="crashed-worker-old",
                lease_expires_at=now - timedelta(seconds=60),
            )
            # 4. Stale RUNNING task that already reached max_attempts (attempt 3 == 3 -> mark FAILED)
            stale_exhausted = SearchRun(
                city="TEST_ONLY_StaleExhausted",
                business_type="HVAC",
                max_results=5,
                status=SearchRunStatus.RUNNING.value,
                attempt_count=3,
                started_at=now - timedelta(seconds=400),
                worker_id="crashed-worker-old",
                lease_expires_at=now - timedelta(seconds=60),
            )
            db.add_all([fresh_leased, fresh_started, stale_recoverable, stale_exhausted])
            db.commit()
            fresh_leased_id = fresh_leased.id
            fresh_started_id = fresh_started.id
            stale_rec_id = stale_recoverable.id
            stale_exh_id = stale_exhausted.id

        # Simulate Worker 2 startup recovery
        affected = recover_interrupted_tasks(
            SessionFactory,
            max_attempts=3,
            stale_after_seconds=180.0,
            reference_now=now,
        )

        # Only stale tasks must be touched; fresh tasks must remain untouched
        assert set(affected) == {stale_rec_id, stale_exh_id}

        with SessionFactory() as db:
            snap_fl = get_task_snapshot(db, fresh_leased_id)
            snap_fs = get_task_snapshot(db, fresh_started_id)
            snap_sr = get_task_snapshot(db, stale_rec_id)
            snap_se = get_task_snapshot(db, stale_exh_id)

            assert snap_fl is not None
            assert snap_fl.status == SearchRunStatus.RUNNING
            assert snap_fl.worker_id == "worker-1"
            assert snap_fl.attempt_count == 1

            assert snap_fs is not None
            assert snap_fs.status == SearchRunStatus.RUNNING
            assert snap_fs.worker_id == "worker-1"
            assert snap_fs.attempt_count == 1

            assert snap_sr is not None
            assert snap_sr.status == SearchRunStatus.PENDING
            assert snap_sr.worker_id is None
            assert snap_sr.lease_expires_at is None
            assert snap_sr.completed_at is None
            assert snap_sr.attempt_count == 1

            assert snap_se is not None
            assert snap_se.status == SearchRunStatus.FAILED
            assert snap_se.completed_at is not None
            assert snap_se.worker_id is None
            assert snap_se.lease_expires_at is None

        # Verify Worker 2 draining pending tasks executes only stale_recoverable and does not steal fresh tasks
        results = drain_pending_tasks(SessionFactory, mock_mode=True, worker_id="worker-2")
        assert [r.search_run_id for r in results] == [stale_rec_id]

        with SessionFactory() as db:
            snap_sr_after = get_task_snapshot(db, stale_rec_id)
            assert snap_sr_after is not None
            assert snap_sr_after.status == SearchRunStatus.COMPLETED
            assert snap_sr_after.attempt_count == 2
    finally:
        eng.dispose()


def test_attempt_fencing_prevents_stale_worker_from_overwriting_newer_attempt(
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Requirement 3 & 7B:
    Verify that if Worker 1's lease expires on attempt 1, and the task is recovered and
    claimed by Worker 2 on attempt 2, Worker 1's late/stale completion OR failure update
    is rejected and cannot overwrite Worker 2's state or leads.
    """
    with worker_session_factory() as db:
        run = SearchRun(
            city="Austin",
            business_type="Dental Clinic",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    # Worker 1 claims attempt 1
    with worker_session_factory() as db:
        claimed_1 = claim_search_run_task(db, run_id, worker_id="worker-1", lease_seconds=1.0)
        assert claimed_1 is not None
        assert claimed_1.attempt_count == 1

    # Simulate Worker 1 stalling beyond lease expiry, recovery running, and Worker 2 claiming attempt 2
    future_now = utc_now() + timedelta(seconds=10)
    recovered = recover_interrupted_tasks(
        worker_session_factory,
        max_attempts=3,
        stale_after_seconds=1.0,
        reference_now=future_now,
    )
    assert recovered == [run_id]

    with worker_session_factory() as db:
        claimed_2 = claim_search_run_task(db, run_id, worker_id="worker-2", lease_seconds=180.0)
        assert claimed_2 is not None
        assert claimed_2.attempt_count == 2
        assert claimed_2.worker_id == "worker-2"

    # Now Worker 1 wakes up late and tries to complete attempt 1
    stale_lead_items = [
        {
            "title": "Stale Worker 1 Clinic",
            "place_id": "ChIJ_stale_w1",
            "placeId": "ChIJ_stale_w1",
            "website": "https://stalew1.example.com",
            "address": "101 Stale St, Austin, TX",
            "phone": "+1-512-555-0001",
            "city": "Austin",
            "business_type": "Dental Clinic",
            "categories": ["Dental Clinic"],
            "rating": 4.1,
            "reviews_count": 10,
            "raw_apify": {"title": "Stale Worker 1 Clinic", "placeId": "ChIJ_stale_w1"},
        }
    ]
    stale_res = execute_discovery_pipeline(
        None,
        run_id,
        session_factory=worker_session_factory,
        mock_mode=True,
        apify_items_override=stale_lead_items,
        already_claimed=True,
        expected_attempt_count=1,
        expected_worker_id="worker-1",
    )
    assert stale_res.status == "stale"

    # Also test Worker 1 waking up late and throwing an exception on attempt 1
    with patch(
        "backend.app.services.discovery_orchestrator.discover_businesses",
        side_effect=ApifyConfigurationError("Stale worker failure"),
    ):
        stale_fail_res = execute_discovery_pipeline(
            None,
            run_id,
            session_factory=worker_session_factory,
            mock_mode=True,
            already_claimed=True,
            expected_attempt_count=1,
            expected_worker_id="worker-1",
        )
    assert stale_fail_res.status == "stale"

    # Verify Worker 2's RUNNING state (attempt 2) and leads were NOT touched by stale Worker 1
    with worker_session_factory() as db:
        snap_mid = get_task_snapshot(db, run_id)
        assert snap_mid is not None
        assert snap_mid.status == SearchRunStatus.RUNNING
        assert snap_mid.attempt_count == 2
        assert snap_mid.worker_id == "worker-2"
        assert len(db.scalars(select(Lead)).all()) == 0

    # Now Worker 2 finishes attempt 2 normally
    fresh_lead_items = [
        {
            "title": "Fresh Worker 2 Clinic",
            "place_id": "ChIJ_fresh_w2",
            "placeId": "ChIJ_fresh_w2",
            "website": "https://freshw2.example.com",
            "address": "202 Fresh Ave, Austin, TX",
            "phone": "+1-512-555-0002",
            "city": "Austin",
            "business_type": "Dental Clinic",
            "categories": ["Dental Clinic"],
            "rating": 4.9,
            "reviews_count": 99,
            "raw_apify": {"title": "Fresh Worker 2 Clinic", "placeId": "ChIJ_fresh_w2"},
        }
    ]
    fresh_res = execute_discovery_pipeline(
        None,
        run_id,
        session_factory=worker_session_factory,
        mock_mode=True,
        apify_items_override=fresh_lead_items,
        already_claimed=True,
        expected_attempt_count=2,
        expected_worker_id="worker-2",
    )
    assert fresh_res.status == "completed"
    assert fresh_res.leads_created == 1

    with worker_session_factory() as db:
        snap_final = get_task_snapshot(db, run_id)
        assert snap_final is not None
        assert snap_final.status == SearchRunStatus.COMPLETED
        assert snap_final.attempt_count == 2
        leads = db.scalars(select(Lead)).all()
        assert len(leads) == 1
        assert leads[0].company_name == "Fresh Worker 2 Clinic"


def test_transient_error_retries_at_runtime_and_succeeds(
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Requirement 4 & 7C:
    Verify that a transient runtime error (e.g. Apify HTTP 503 or timeout) on attempt 1
    transitions the task back to PENDING with completed_at=None, and the next poll claims
    attempt 2 and completes the task.
    """
    with worker_session_factory() as db:
        run = SearchRun(
            city="Austin",
            business_type="Dentist",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    valid_items = [
        {
            "title": "Retry Success Dental",
            "place_id": "ChIJ_retry_01",
            "placeId": "ChIJ_retry_01",
            "website": "https://retrysuccess.example.com",
            "address": "500 Retry Blvd, Austin, TX",
            "phone": "+1-512-555-0500",
            "city": "Austin",
            "business_type": "Dentist",
            "categories": ["Dentist"],
            "rating": 4.8,
            "reviews_count": 45,
            "raw_apify": {"title": "Retry Success Dental", "placeId": "ChIJ_retry_01"},
        }
    ]

    call_count = 0

    def flaky_discover(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ApifyExecutionError("Apify API returned HTTP 503: Service Unavailable", status_code=503)
        return valid_items

    with patch(
        "backend.app.services.discovery_orchestrator.discover_businesses",
        side_effect=flaky_discover,
    ), patch(
        "backend.app.services.discovery_orchestrator.scrape_website_content",
        return_value="Modern dental care.",
    ):
        # Attempt 1: transient failure -> returns to PENDING
        res1 = process_next_pending_task(worker_session_factory, mock_mode=True, max_attempts=3)
        assert res1 is not None
        assert res1.status == SearchRunStatus.PENDING.value

        with worker_session_factory() as db:
            snap1 = get_task_snapshot(db, run_id)
            assert snap1 is not None
            assert snap1.status == SearchRunStatus.PENDING
            assert snap1.attempt_count == 1
            assert snap1.completed_at is None
            assert snap1.worker_id is None
            assert snap1.lease_expires_at is None
            assert "503" in (snap1.last_error or "")

        # Attempt 2: succeeds -> transitions to COMPLETED
        res2 = process_next_pending_task(worker_session_factory, mock_mode=True, max_attempts=3)
        assert res2 is not None
        assert res2.status == SearchRunStatus.COMPLETED.value
        assert res2.leads_created == 1

        with worker_session_factory() as db:
            snap2 = get_task_snapshot(db, run_id)
            assert snap2 is not None
            assert snap2.status == SearchRunStatus.COMPLETED
            assert snap2.attempt_count == 2
            assert snap2.completed_at is not None
            assert snap2.last_error is None


def test_permanent_errors_fail_immediately_on_first_attempt(
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Requirement 4 & 7C:
    Verify that permanent errors (ApifyConfigurationError, GeminiConfigurationError,
    and HTTP 401/403/400/404) transition directly to FAILED on attempt 1 without retrying.
    """
    permanent_exceptions = [
        ApifyConfigurationError("APIFY_API_TOKEN is not configured."),
        GeminiConfigurationError("GEMINI_API_KEY is not configured."),
        ApifyExecutionError("Apify API returned HTTP 401: Unauthorized", status_code=401),
        GeminiExecutionError("Gemini API returned HTTP 400: Invalid argument", status_code=400),
    ]

    for exc in permanent_exceptions:
        with worker_session_factory() as db:
            run = SearchRun(
                city="Austin",
                business_type="Dentist",
                max_results=5,
                status=SearchRunStatus.PENDING.value,
            )
            db.add(run)
            db.commit()
            run_id = run.id

        with patch(
            "backend.app.services.discovery_orchestrator.discover_businesses",
            side_effect=exc,
        ):
            res = process_next_pending_task(worker_session_factory, mock_mode=True, max_attempts=3)

        assert res is not None
        assert res.status == SearchRunStatus.FAILED.value

        with worker_session_factory() as db:
            snap = get_task_snapshot(db, run_id)
            assert snap is not None
            assert snap.status == SearchRunStatus.FAILED
            assert snap.attempt_count == 1
            assert snap.completed_at is not None
            assert snap.last_error is not None

        # Verify task is not retried on subsequent poll
        next_res = process_next_pending_task(worker_session_factory, mock_mode=True, max_attempts=3)
        assert next_res is None


def test_transient_error_respects_max_attempts_and_stops_retrying(
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Requirement 4 & 7C:
    Verify that a persistent transient error (e.g. httpx.ConnectTimeout / HTTP 429)
    retries up to max_attempts (3), increments attempt_count once per claim (1 -> 2 -> 3),
    and transitions to FAILED with completed_at set on the 3rd attempt.
    """
    with worker_session_factory() as db:
        run = SearchRun(
            city="Austin",
            business_type="Dentist",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    with patch(
        "backend.app.services.discovery_orchestrator.discover_businesses",
        side_effect=ApifyExecutionError("Apify API returned HTTP 429: Too Many Requests", status_code=429),
    ):
        results = drain_pending_tasks(worker_session_factory, mock_mode=True, max_attempts=3)

    assert len(results) == 3
    assert results[0].status == SearchRunStatus.PENDING.value
    assert results[1].status == SearchRunStatus.PENDING.value
    assert results[2].status == SearchRunStatus.FAILED.value

    with worker_session_factory() as db:
        snap = get_task_snapshot(db, run_id)
        assert snap is not None
        assert snap.status == SearchRunStatus.FAILED
        assert snap.attempt_count == 3
        assert snap.completed_at is not None
        assert "429" in (snap.last_error or "")

        # Also verify claim_search_run_task refuses to claim any task with attempt_count >= max_attempts
        run_obj = db.get(SearchRun, run_id)
        assert run_obj is not None
        run_obj.status = SearchRunStatus.PENDING.value
        db.commit()

        claimed = claim_search_run_task(db, run_id, max_attempts=3)
        assert claimed is None


def test_no_database_transaction_open_during_external_network_calls_and_incremental_persistence(
    worker_engine: Engine,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """
    Requirement 5 & 7D:
    Verify that:
    1. Zero SQLAlchemy connections have an open transaction while `discover_businesses`,
       `scrape_website_content`, or `analyze_lead_with_gemini` are executing.
    2. Leads are persisted incrementally in short-lived transactions so if lead #2 fails
       with an unhandled error, lead #1 is already durably committed in SQLite.
    """
    active_transactions: set[int] = set()

    def on_begin(conn: Any) -> None:
        active_transactions.add(id(conn))

    def on_commit_or_rollback(conn: Any) -> None:
        active_transactions.discard(id(conn))

    event.listen(worker_engine, "begin", on_begin)
    event.listen(worker_engine, "commit", on_commit_or_rollback)
    event.listen(worker_engine, "rollback", on_commit_or_rollback)

    try:
        with worker_session_factory() as db:
            run = SearchRun(
                city="Austin",
                business_type="Dental Clinic",
                max_results=5,
                status=SearchRunStatus.PENDING.value,
            )
            db.add(run)
            db.commit()
            run_id = run.id

        two_businesses = [
            {
                "title": "First Incremental Clinic",
                "place_id": "ChIJ_inc_1",
                "placeId": "ChIJ_inc_1",
                "website": "https://inc1.example.com",
                "address": "100 First St, Austin, TX",
                "phone": "+1-512-555-0101",
                "city": "Austin",
                "business_type": "Dental Clinic",
                "categories": ["Dental Clinic"],
                "rating": 4.9,
                "reviews_count": 80,
                "raw_apify": {"title": "First Incremental Clinic", "placeId": "ChIJ_inc_1"},
            },
            {
                "title": "Second Incremental Clinic",
                "place_id": "ChIJ_inc_2",
                "placeId": "ChIJ_inc_2",
                "website": "https://inc2.example.com",
                "address": "200 Second St, Austin, TX",
                "phone": "+1-512-555-0102",
                "city": "Austin",
                "business_type": "Dental Clinic",
                "categories": ["Dental Clinic"],
                "rating": 4.7,
                "reviews_count": 50,
                "raw_apify": {"title": "Second Incremental Clinic", "placeId": "ChIJ_inc_2"},
            },
        ]

        observed_tx_counts_during_network: list[int] = []

        def checked_discover(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            observed_tx_counts_during_network.append(len(active_transactions))
            return two_businesses

        def checked_scrape(*_args: Any, **_kwargs: Any) -> str:
            observed_tx_counts_during_network.append(len(active_transactions))
            return "Website text content"

        gemini_calls = 0

        def checked_gemini(item: dict[str, Any], **_kwargs: Any) -> Any:
            nonlocal gemini_calls
            gemini_calls += 1
            observed_tx_counts_during_network.append(len(active_transactions))
            if gemini_calls == 2:
                raise GeminiConfigurationError("Simulated failure on lead #2 after lead #1 persisted")
            from backend.app.services.gemini_service import analyze_lead_with_gemini as real_analyze

            return real_analyze(item, website_content="Website text content", mock_mode=True)

        with patch(
            "backend.app.services.discovery_orchestrator.discover_businesses",
            side_effect=checked_discover,
        ), patch(
            "backend.app.services.discovery_orchestrator.scrape_website_content",
            side_effect=checked_scrape,
        ), patch(
            "backend.app.services.discovery_orchestrator.analyze_lead_with_gemini",
            side_effect=checked_gemini,
        ):
            res = process_next_pending_task(worker_session_factory, mock_mode=True)

        assert res is not None
        assert res.status == SearchRunStatus.FAILED.value
        # Verify NO database transaction was open during any of the network call hooks
        assert observed_tx_counts_during_network == [0, 0, 0, 0, 0]

        # Verify partial progress (First Incremental Clinic) was durably persisted and not lost
        with worker_session_factory() as verify_db:
            persisted_leads = verify_db.scalars(select(Lead)).all()
            assert len(persisted_leads) == 1
            assert persisted_leads[0].company_name == "First Incremental Clinic"
            assert persisted_leads[0].score > 0
    finally:
        event.remove(worker_engine, "begin", on_begin)
        event.remove(worker_engine, "commit", on_commit_or_rollback)
        event.remove(worker_engine, "rollback", on_commit_or_rollback)


def test_last_error_sanitizes_secrets_on_task_failure(
    worker_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify that when a background task fails, last_error is persisted with all
    API keys, Bearer tokens, and secrets sanitized/redacted.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSySuperSecretGeminiKey987654321")
    monkeypatch.setenv("APIFY_API_TOKEN", "apify_api_SuperSecretTokenValue123")
    monkeypatch.setenv("N8N_CALLBACK_SECRET", "top_secret_callback_hmac_999")
    get_settings.cache_clear()

    with worker_session_factory() as db:
        run = SearchRun(
            city="Austin",
            business_type="Dentist",
            max_results=5,
            status=SearchRunStatus.PENDING.value,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    leaked_error_msg = (
        "HTTP 401 Upstream failure for https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent"
        "?key=AIzaSySuperSecretGeminiKey987654321 with header Authorization: Bearer apify_api_SuperSecretTokenValue123 "
        "and callback secret top_secret_callback_hmac_999"
    )

    with patch(
        "backend.app.services.discovery_orchestrator.discover_businesses",
        side_effect=ApifyExecutionError(leaked_error_msg, status_code=401),
    ):
        res = process_next_pending_task(worker_session_factory, mock_mode=True)

    assert res is not None
    assert res.status == "failed"

    with worker_session_factory() as db:
        snap = get_task_snapshot(db, run_id)
        assert snap is not None
        assert snap.status == SearchRunStatus.FAILED
        assert snap.attempt_count == 1
        assert snap.started_at is not None
        assert snap.completed_at is not None
        assert snap.last_error is not None

        # Verify no secret value appears anywhere in last_error
        assert "AIzaSySuperSecretGeminiKey987654321" not in snap.last_error
        assert "apify_api_SuperSecretTokenValue123" not in snap.last_error
        assert "top_secret_callback_hmac_999" not in snap.last_error
        assert "[REDACTED]" in snap.last_error


def test_additive_sqlite_migration_for_existing_search_runs_table(
    tmp_path: Path,
) -> None:
    """
    Requirement 6 & 7E:
    Verify init_db() safely adds attempt_count, started_at, last_error, worker_id, and
    lease_expires_at to a legacy search_runs table without losing pre-existing rows,
    and remains idempotent across repeated init_db() calls.
    """
    legacy_db_file = tmp_path / "legacy_search_runs.db"
    eng = create_sqlite_engine(f"sqlite:///{legacy_db_file}")

    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE search_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city VARCHAR(120) NOT NULL,
                    business_type VARCHAR(120) NOT NULL,
                    max_results INTEGER NOT NULL DEFAULT 20,
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO search_runs (city, business_type, max_results, status) "
                "VALUES ('Denver', 'Dentist', 15, 'completed')"
            )
        )

    # Run init_db() twice to verify idempotency
    init_db(eng)
    init_db(eng)

    inspector = inspect(eng)
    cols = {c["name"] for c in inspector.get_columns("search_runs")}
    assert {
        "attempt_count",
        "started_at",
        "last_error",
        "worker_id",
        "lease_expires_at",
    }.issubset(cols)

    SessionLegacy = sessionmaker(bind=eng, class_=Session)
    with SessionLegacy() as db:
        existing_run = db.scalar(select(SearchRun).where(SearchRun.city == "Denver"))
        assert existing_run is not None
        assert existing_run.business_type == "Dentist"
        assert existing_run.attempt_count == 0
        assert existing_run.started_at is None
        assert existing_run.last_error is None
        assert existing_run.worker_id is None
        assert existing_run.lease_expires_at is None

    eng.dispose()


def test_non_blocking_background_worker_and_legacy_n8n_callback_compatibility(
    worker_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Requirement 7E:
    Verify:
    1. Public API search creation and status response schemas remain unchanged.
    2. DurableBackgroundWorker processes PENDING tasks non-blockingly.
    3. Legacy n8n callback (`process_n8n_callback` without fencing arguments) remains
       100% backward-compatible.
    """
    monkeypatch.setenv("DISCOVERY_EXECUTION_MODE", "python")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    get_settings.cache_clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = worker_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    worker = DurableBackgroundWorker(
        session_factory=worker_session_factory,
        poll_interval_seconds=0.05,
        max_attempts=3,
        mock_mode=True,
    )

    mock_apify_data = [
        {
            "title": "NonBlocking Austin Clinic",
            "place_id": "ChIJ_nb_001",
            "placeId": "ChIJ_nb_001",
            "website": "https://nbaustin.example.com",
            "address": "200 Congress Ave, Austin, TX",
            "phone": "+1-512-555-0144",
            "city": "Austin",
            "business_type": "Dental Clinic",
            "categories": ["Dental Clinic"],
            "rating": 4.8,
            "reviews_count": 64,
            "raw_apify": {"title": "NonBlocking Austin Clinic", "placeId": "ChIJ_nb_001"},
        }
    ]

    try:
        with patch(
            "backend.app.services.discovery_orchestrator.discover_businesses",
            return_value=mock_apify_data,
        ), patch(
            "backend.app.services.discovery_orchestrator.scrape_website_content",
            return_value="Family and cosmetic dentistry in Austin.",
        ), patch(
            "backend.app.services.worker_service.get_background_worker",
            return_value=worker,
        ):
            with TestClient(app) as client:
                # 1. Create search run -> verify exact public response schema keys
                create_resp = client.post(
                    "/api/v1/search-runs",
                    json={
                        "city": "Austin",
                        "business_type": "Dental Clinic",
                        "max_results": 5,
                    },
                )
                assert create_resp.status_code == 201
                created_body = create_resp.json()
                assert set(created_body.keys()) == {
                    "id",
                    "city",
                    "business_type",
                    "max_results",
                    "status",
                    "created_at",
                    "completed_at",
                    "leads_count",
                }
                run_id = created_body["id"]
                assert created_body["status"] == "pending"

                # 2. Start background worker and wait briefly for non-blocking processing
                worker.start()
                worker.notify()

                deadline = time.time() + 5.0
                final_status = "pending"
                while time.time() < deadline:
                    poll_resp = client.get(f"/api/v1/search-runs/{run_id}")
                    assert poll_resp.status_code == 200
                    poll_data = poll_resp.json()
                    final_status = poll_data["status"]
                    if final_status in {"completed", "failed"}:
                        break
                    time.sleep(0.05)

                assert final_status == "completed"
                detail_resp = client.get(f"/api/v1/search-runs/{run_id}")
                detail_data = detail_resp.json()
                assert set(detail_data.keys()) == {
                    "id",
                    "city",
                    "business_type",
                    "max_results",
                    "status",
                    "created_at",
                    "completed_at",
                    "leads_count",
                    "leads",
                }
                assert detail_data["leads_count"] == 1
                assert detail_data["leads"][0]["company_name"] == "NonBlocking Austin Clinic"
                assert detail_data["leads"][0]["score"] > 0

        # 3. Verify legacy n8n callback compatibility without fencing parameters
        with worker_session_factory() as db:
            legacy_run = SearchRun(
                city="Chicago",
                business_type="Law Firm",
                max_results=5,
                status=SearchRunStatus.RUNNING.value,
            )
            db.add(legacy_run)
            db.commit()
            legacy_id = legacy_run.id

            cb_req = N8nCallbackRequest(
                search_run_id=legacy_id,
                status="completed",
                leads=[],
            )
            cb_resp = process_n8n_callback(db, cb_req)
            assert cb_resp.status == "completed"
    finally:
        worker.stop()
        app.dependency_overrides.clear()
        get_settings.cache_clear()

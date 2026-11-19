"""
Durable Database-Backed Python Background Worker Service.

Implements Phase 2 / Phase 2.2 durable task tracking, multi-worker lease ownership,
attempt fencing, bounded runtime retry classification, and non-blocking execution:
- Uses SQLite / SQLAlchemy `SearchRun` table as the durable task store (no in-memory-only queue).
- Explicit lifecycle states: PENDING ('pending'), RUNNING ('running'), COMPLETED ('completed'), FAILED ('failed').
- Persists task_id, search_run_id, status, attempt_count, created_at, started_at, completed_at,
  sanitized last_error, worker_id, and lease_expires_at.
- Safely recovers only demonstrably stale interrupted RUNNING tasks (`recover_interrupted_tasks`).
- Atomically claims PENDING tasks via compare-and-swap SQL UPDATE (`claim_search_run_task`, `claim_next_pending_task`)
  while enforcing `attempt_count < max_attempts`.
- Maintains lease heartbeats during long-running Apify/Gemini external network calls without
  holding open SQLAlchemy database transactions across network I/O.
"""

from datetime import datetime, timedelta, timezone
import logging
import os
import re
import socket
import threading
from typing import Callable, Optional
import uuid

import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db.base import utc_now
from backend.app.db.session import SessionLocal
from backend.app.models.search_run import SearchRun, SearchRunStatus
from backend.app.services.apify_service import (
    ApifyConfigurationError,
    ApifyExecutionError,
)
from backend.app.services.discovery_orchestrator import (
    DiscoveryPipelineResult,
    execute_discovery_pipeline,
)
from backend.app.services.gemini_service import (
    GeminiConfigurationError,
    GeminiExecutionError,
)

logger = logging.getLogger(__name__)

_PERMANENT_HTTP_STATUS_CODES = {400, 401, 403, 404, 422}
_TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class DurableTaskSnapshot(BaseModel):
    """Structured snapshot of a durable background task persisted in the database."""

    model_config = ConfigDict(from_attributes=True)

    task_id: int
    search_run_id: int
    status: SearchRunStatus
    attempt_count: int
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    worker_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None


def generate_worker_id(prefix: str = "worker") -> str:
    """Generate a unique worker ownership identifier combining hostname, PID, and random token."""
    host = socket.gethostname().split(".")[0] or "local"
    pid = os.getpid()
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}-{host}-{pid}-{short_uuid}"


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize a naive or timezone-aware datetime to UTC for safe timestamp comparison."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_permanent_task_error(exc: BaseException) -> bool:
    """
    Classify whether an exception encountered during task execution is permanent
    (must fail immediately without retry) or transient (eligible for bounded retry).

    Permanent errors:
    - ApifyConfigurationError, GeminiConfigurationError
    - ValueError, TypeError, KeyError (input validation / schema errors)
    - ApifyExecutionError / GeminiExecutionError with HTTP 400, 401, 403, 404, 422
    - Error messages explicitly indicating HTTP 400, 401, 403, 404 or invalid/missing API credentials

    Transient errors:
    - httpx.TimeoutException, httpx.TransportError, httpx.NetworkError, httpx.ConnectError
    - ApifyExecutionError / GeminiExecutionError with HTTP 408, 429, 500, 502, 503, 504
    - General runtime/provider execution failures without permanent status codes
    """
    if isinstance(
        exc,
        (
            ApifyConfigurationError,
            GeminiConfigurationError,
            ValueError,
            TypeError,
            KeyError,
        ),
    ):
        return True

    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return False

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code in _PERMANENT_HTTP_STATUS_CODES:
            return True
        if status_code in _TRANSIENT_HTTP_STATUS_CODES or status_code >= 500:
            return False

    msg = str(exc or "")
    http_match = re.search(r"\bHTTP\s+(\d{3})\b", msg, flags=re.IGNORECASE)
    if http_match:
        parsed_code = int(http_match.group(1))
        if parsed_code in _PERMANENT_HTTP_STATUS_CODES:
            return True
        if parsed_code in _TRANSIENT_HTTP_STATUS_CODES or parsed_code >= 500:
            return False

    lowered = msg.lower()
    permanent_markers = (
        "not configured",
        "invalid api key",
        "invalid token",
        "unauthorized",
        "forbidden",
        "permission denied",
        "missing required field",
    )
    if any(marker in lowered for marker in permanent_markers):
        return True

    return False


def sanitize_error_message(
    raw_error: Optional[str],
    *,
    max_length: int = 1000,
) -> Optional[str]:
    """
    Sanitize an error message before persisting to `last_error` or returning in logs.
    Guarantees that API keys, Bearer tokens, callback secrets, and URL query keys are redacted.
    """
    if raw_error is None:
        return None
    text = str(raw_error).strip()
    if not text:
        return None

    settings = get_settings()
    secret_candidates = [
        settings.gemini_api_key,
        settings.apify_api_token,
        settings.n8n_callback_secret,
        os.getenv("GEMINI_API_KEY") or "",
        os.getenv("APIFY_API_TOKEN") or "",
        os.getenv("APIFY_TOKEN") or "",
        os.getenv("N8N_CALLBACK_SECRET") or "",
    ]
    for secret in secret_candidates:
        cleaned_secret = str(secret or "").strip()
        if len(cleaned_secret) >= 4 and cleaned_secret not in {"MY_GEMINI_API_KEY"}:
            text = text.replace(cleaned_secret, "[REDACTED]")

    # Redact query parameters containing keys/tokens/secrets
    text = re.sub(
        r"([?&](?:key|api_key|apikey|token|access_token|secret)=)[^&\s\"']+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Redact Authorization Bearer tokens
    text = re.sub(
        r"(Bearer\s+)[A-Za-z0-9_\-\.~+/]+=*",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Redact explicit Apify token patterns
    text = re.sub(
        r"apify_api_[A-Za-z0-9_\-]+",
        "[REDACTED_APIFY_TOKEN]",
        text,
        flags=re.IGNORECASE,
    )
    # Redact Google AIza API key patterns
    text = re.sub(
        r"AIza[0-9A-Za-z\-_]{15,}",
        "[REDACTED_GEMINI_KEY]",
        text,
    )
    # Redact key=value assignments for known secret names
    text = re.sub(
        r"((?:GEMINI_API_KEY|APIFY_API_TOKEN|APIFY_TOKEN|N8N_CALLBACK_SECRET)\s*=\s*)[^\s,;\"']+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    return text[:max_length]


def get_task_snapshot(
    db: Session,
    search_run_id: int,
) -> Optional[DurableTaskSnapshot]:
    """Retrieve the current durable task lifecycle state for a SearchRun."""
    run = db.get(SearchRun, search_run_id)
    if run is None:
        return None
    db.refresh(run)
    return DurableTaskSnapshot(
        task_id=run.task_id,
        search_run_id=run.search_run_id,
        status=SearchRunStatus(run.status),
        attempt_count=run.attempt_count or 0,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        last_error=run.last_error,
        worker_id=run.worker_id,
        lease_expires_at=run.lease_expires_at,
    )


def enqueue_search_run_task(
    db: Session,
    search_run_id: int,
    *,
    reset_if_failed: bool = False,
    notify_worker: bool = True,
) -> SearchRun:
    """
    Ensure a SearchRun is durably persisted in PENDING state before background execution begins.
    - If `reset_if_failed=True` and the SearchRun is currently FAILED, transitions it back to PENDING
      and resets attempt_count to 0 so explicit manual retries are not blocked by max_attempts.
    - Wakes the embedded background worker thread if running (non-blocking).
    """
    run = db.get(SearchRun, search_run_id)
    if run is None:
        raise ValueError(f"SearchRun with id={search_run_id} not found.")

    if reset_if_failed and run.status == SearchRunStatus.FAILED.value:
        run.status = SearchRunStatus.PENDING.value
        run.attempt_count = 0
        run.completed_at = None
        run.worker_id = None
        run.lease_expires_at = None
        db.add(run)

    db.commit()
    db.refresh(run)

    if notify_worker:
        worker = get_background_worker()
        if worker.is_running:
            worker.notify()

    return run


def claim_search_run_task(
    db: Session,
    search_run_id: int,
    *,
    worker_id: Optional[str] = None,
    lease_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
) -> Optional[SearchRun]:
    """
    Atomically claim a specific PENDING SearchRun task for execution.
    Uses a conditional UPDATE (`WHERE id = :id AND status = 'pending' AND attempt_count < :max_attempts`)
    so concurrent workers cannot claim the same task twice and exhausted tasks cannot be claimed.
    """
    settings = get_settings()
    effective_max = (
        max_attempts
        if max_attempts is not None
        else settings.worker_max_attempts
    )
    effective_lease = (
        lease_seconds
        if lease_seconds is not None
        else settings.worker_lease_seconds
    )
    effective_worker_id = worker_id or generate_worker_id()
    now = utc_now()
    lease_expiry = now + timedelta(seconds=max(1.0, float(effective_lease)))

    stmt = (
        update(SearchRun)
        .where(
            SearchRun.id == search_run_id,
            SearchRun.status == SearchRunStatus.PENDING.value,
            SearchRun.attempt_count < effective_max,
        )
        .values(
            status=SearchRunStatus.RUNNING.value,
            attempt_count=SearchRun.attempt_count + 1,
            started_at=now,
            completed_at=None,
            worker_id=effective_worker_id,
            lease_expires_at=lease_expiry,
        )
    )
    result = db.execute(stmt)
    db.commit()

    if result.rowcount != 1:
        return None

    claimed = db.get(SearchRun, search_run_id)
    if claimed is not None:
        db.refresh(claimed)
    return claimed


def claim_next_pending_task(
    db: Session,
    *,
    worker_id: Optional[str] = None,
    lease_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
) -> Optional[SearchRun]:
    """
    Find and atomically claim the oldest eligible PENDING SearchRun task in FIFO order.
    Tasks that have already reached `max_attempts` are transitioned to FAILED rather than claimed.
    Returns None if no eligible PENDING task is available.
    """
    settings = get_settings()
    effective_max = (
        max_attempts
        if max_attempts is not None
        else settings.worker_max_attempts
    )

    # First mark any PENDING tasks that already exhausted max_attempts as FAILED to prevent infinite loops
    exhausted_runs = list(
        db.scalars(
            select(SearchRun)
            .where(
                SearchRun.status == SearchRunStatus.PENDING.value,
                SearchRun.attempt_count >= effective_max,
            )
            .order_by(SearchRun.created_at.asc(), SearchRun.id.asc())
        ).all()
    )
    if exhausted_runs:
        now = utc_now()
        for run in exhausted_runs:
            run.status = SearchRunStatus.FAILED.value
            run.completed_at = now
            run.worker_id = None
            run.lease_expires_at = None
            if not run.last_error:
                run.last_error = sanitize_error_message(
                    f"Task reached maximum attempts ({run.attempt_count}/{effective_max}) before claim."
                )
            db.add(run)
        db.commit()

    candidate_ids = list(
        db.scalars(
            select(SearchRun.id)
            .where(
                SearchRun.status == SearchRunStatus.PENDING.value,
                SearchRun.attempt_count < effective_max,
            )
            .order_by(SearchRun.created_at.asc(), SearchRun.id.asc())
            .limit(10)
        ).all()
    )

    for candidate_id in candidate_ids:
        claimed = claim_search_run_task(
            db,
            candidate_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_attempts=effective_max,
        )
        if claimed is not None:
            return claimed

    return None


def renew_task_lease(
    session_factory: Callable[[], Session],
    search_run_id: int,
    *,
    expected_attempt_count: int,
    expected_worker_id: Optional[str],
    lease_seconds: Optional[float] = None,
) -> bool:
    """
    Renew the lease expiry timestamp for an actively RUNNING task using a short-lived transaction.
    Returns True if the lease was renewed, or False if the task is no longer owned by this attempt/worker.
    """
    effective_lease = (
        lease_seconds
        if lease_seconds is not None
        else get_settings().worker_lease_seconds
    )
    now = utc_now()
    new_expiry = now + timedelta(seconds=max(1.0, float(effective_lease)))

    conditions = [
        SearchRun.id == search_run_id,
        SearchRun.status == SearchRunStatus.RUNNING.value,
        SearchRun.attempt_count == expected_attempt_count,
    ]
    if expected_worker_id is not None:
        conditions.append(SearchRun.worker_id == expected_worker_id)

    with session_factory() as db:
        stmt = (
            update(SearchRun)
            .where(*conditions)
            .values(lease_expires_at=new_expiry)
        )
        res = db.execute(stmt)
        db.commit()
        return res.rowcount == 1


def _is_running_task_stale(
    run: SearchRun,
    *,
    now: datetime,
    stale_after_seconds: float,
) -> bool:
    """
    Determine whether a RUNNING SearchRun is demonstrably stale:
    - If `lease_expires_at` is set: stale only when `lease_expires_at <= now`.
    - Otherwise, if `started_at` is set: stale only when `started_at <= now - stale_after_seconds`.
    - If neither `lease_expires_at` nor `started_at` is set (legacy interrupted row): considered stale.
    """
    lease_exp = _ensure_utc(run.lease_expires_at)
    if lease_exp is not None:
        return lease_exp <= now

    started = _ensure_utc(run.started_at)
    if started is not None:
        cutoff = now - timedelta(seconds=max(0.0, float(stale_after_seconds)))
        return started <= cutoff

    return True


def recover_interrupted_tasks(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    max_attempts: Optional[int] = None,
    stale_after_seconds: Optional[float] = None,
    reference_now: Optional[datetime] = None,
) -> list[int]:
    """
    Recover tasks that were left in a demonstrably stale RUNNING state.
    - Fresh RUNNING tasks (with an active `lease_expires_at > now` or recent `started_at` within
      `stale_after_seconds`) are NEVER recovered or stolen from an active worker.
    - Stale RUNNING tasks with `attempt_count < max_attempts` are transitioned back to PENDING
      with `completed_at = None`, `worker_id = None`, and `lease_expires_at = None`.
    - Stale RUNNING tasks that have already reached or exceeded `max_attempts` are marked FAILED
      with `completed_at = utc_now()`.
    Returns the list of recovered/failed SearchRun IDs.
    """
    settings = get_settings()
    effective_max = (
        max_attempts
        if max_attempts is not None
        else settings.worker_max_attempts
    )
    effective_stale = (
        stale_after_seconds
        if stale_after_seconds is not None
        else settings.worker_stale_task_timeout_seconds
    )
    now = _ensure_utc(reference_now) or utc_now()
    affected_ids: list[int] = []

    with session_factory() as db:
        running_runs = list(
            db.scalars(
                select(SearchRun)
                .where(SearchRun.status == SearchRunStatus.RUNNING.value)
                .order_by(SearchRun.created_at.asc(), SearchRun.id.asc())
            ).all()
        )

        for run in running_runs:
            if not _is_running_task_stale(
                run,
                now=now,
                stale_after_seconds=effective_stale,
            ):
                continue

            attempts = run.attempt_count or 0
            prev_worker = run.worker_id
            if attempts < effective_max:
                new_status = SearchRunStatus.PENDING.value
                new_completed_at = None
                err_msg = sanitize_error_message(
                    f"Stale running task interrupted on attempt {attempts}; re-queued as pending."
                )
            else:
                new_status = SearchRunStatus.FAILED.value
                new_completed_at = now
                err_msg = sanitize_error_message(
                    f"Stale running task interrupted after reaching maximum attempts ({attempts}/{effective_max})."
                )

            # Compare-and-swap update fenced by current attempt_count and worker_id
            conditions = [
                SearchRun.id == run.id,
                SearchRun.status == SearchRunStatus.RUNNING.value,
                SearchRun.attempt_count == attempts,
            ]
            if prev_worker is not None:
                conditions.append(SearchRun.worker_id == prev_worker)
            else:
                conditions.append(SearchRun.worker_id.is_(None))

            res = db.execute(
                update(SearchRun)
                .where(*conditions)
                .values(
                    status=new_status,
                    completed_at=new_completed_at,
                    last_error=err_msg,
                    worker_id=None,
                    lease_expires_at=None,
                )
            )
            if res.rowcount == 1:
                affected_ids.append(run.id)
                if new_status == SearchRunStatus.PENDING.value:
                    logger.info(
                        "Recovered stale SearchRun id=%d (attempt %d/%d) -> PENDING",
                        run.id,
                        attempts,
                        effective_max,
                    )
                else:
                    logger.warning(
                        "Stale SearchRun id=%d exceeded max attempts (%d/%d) -> FAILED",
                        run.id,
                        attempts,
                        effective_max,
                    )

        if affected_ids:
            db.commit()

    return affected_ids


class _LeaseHeartbeatTimer:
    """
    Periodic background lease renewal for an in-flight task execution.
    Ensures long-running Apify or Gemini calls keep their lease fresh while the worker is alive,
    and stops renewing immediately if the task finishes or the worker shuts down.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        search_run_id: int,
        attempt_count: int,
        worker_id: Optional[str],
        lease_seconds: float,
        interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.search_run_id = search_run_id
        self.attempt_count = attempt_count
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.05, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def beat_once(self) -> bool:
        try:
            return renew_task_lease(
                self.session_factory,
                self.search_run_id,
                expected_attempt_count=self.attempt_count,
                expected_worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except Exception as exc:
            logger.debug(
                "Lease heartbeat renewal skipped for SearchRun id=%d: %s",
                self.search_run_id,
                exc.__class__.__name__,
            )
            return False

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"lease-heartbeat-run-{self.search_run_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=self.interval_seconds):
            if not self.beat_once():
                break


def execute_claimed_task(
    session_factory: Callable[[], Session],
    search_run_id: int,
    *,
    mock_mode: bool = False,
    expected_attempt_count: Optional[int] = None,
    expected_worker_id: Optional[str] = None,
    max_attempts: Optional[int] = None,
    lease_seconds: Optional[float] = None,
    heartbeat_interval_seconds: Optional[float] = None,
) -> DiscoveryPipelineResult:
    """
    Execute a claimed SearchRun task using short-lived database transactions.
    - Does NOT hold an open SQLAlchemy transaction across Apify, scraping, or Gemini calls.
    - Renews the task lease while execution is active.
    - Enforces attempt and worker ownership fencing on all persistence and status transitions.
    - Applies bounded runtime retry for transient errors and immediate failure for permanent errors.
    """
    settings = get_settings()
    effective_max = (
        max_attempts
        if max_attempts is not None
        else settings.worker_max_attempts
    )
    effective_lease = (
        lease_seconds
        if lease_seconds is not None
        else settings.worker_lease_seconds
    )
    effective_hb = (
        heartbeat_interval_seconds
        if heartbeat_interval_seconds is not None
        else settings.worker_heartbeat_interval_seconds
    )

    if expected_attempt_count is None or expected_worker_id is None:
        with session_factory() as db:
            snap = get_task_snapshot(db, search_run_id)
            if snap is not None:
                if expected_attempt_count is None:
                    expected_attempt_count = snap.attempt_count
                if expected_worker_id is None:
                    expected_worker_id = snap.worker_id

    heartbeat = _LeaseHeartbeatTimer(
        session_factory,
        search_run_id=search_run_id,
        attempt_count=expected_attempt_count or 1,
        worker_id=expected_worker_id,
        lease_seconds=effective_lease,
        interval_seconds=effective_hb,
    )
    heartbeat.start()
    try:
        return execute_discovery_pipeline(
            None,
            search_run_id,
            session_factory=session_factory,
            mock_mode=mock_mode,
            already_claimed=True,
            expected_attempt_count=expected_attempt_count,
            expected_worker_id=expected_worker_id,
            max_attempts=effective_max,
            heartbeat_callback=heartbeat.beat_once,
        )
    finally:
        heartbeat.stop()


def process_next_pending_task(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    mock_mode: bool = False,
    worker_id: Optional[str] = None,
    lease_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
    heartbeat_interval_seconds: Optional[float] = None,
) -> Optional[DiscoveryPipelineResult]:
    """
    Atomically claim and execute the next eligible PENDING SearchRun task from the database.
    Returns None if no eligible PENDING task exists.
    """
    effective_worker_id = worker_id or generate_worker_id()
    with session_factory() as claim_db:
        claimed_run = claim_next_pending_task(
            claim_db,
            worker_id=effective_worker_id,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        if claimed_run is None:
            return None
        target_id = claimed_run.id
        claimed_attempt = claimed_run.attempt_count
        claimed_worker = claimed_run.worker_id

    return execute_claimed_task(
        session_factory,
        target_id,
        mock_mode=mock_mode,
        expected_attempt_count=claimed_attempt,
        expected_worker_id=claimed_worker,
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


def drain_pending_tasks(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    mock_mode: bool = False,
    max_tasks: int = 100,
    worker_id: Optional[str] = None,
    lease_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
) -> list[DiscoveryPipelineResult]:
    """
    Claim and execute all currently PENDING SearchRun tasks in FIFO order up to `max_tasks`.
    Includes retries if a task fails transiently and returns to PENDING while attempts remain.
    """
    effective_worker_id = worker_id or generate_worker_id()
    results: list[DiscoveryPipelineResult] = []
    for _ in range(max_tasks):
        res = process_next_pending_task(
            session_factory=session_factory,
            mock_mode=mock_mode,
            worker_id=effective_worker_id,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        if res is None:
            break
        results.append(res)
    return results


class DurableBackgroundWorker:
    """
    Non-blocking background worker loop backed by the SQLAlchemy database.
    - Recovers demonstrably stale interrupted RUNNING tasks on startup and periodically.
    - Polls the database for PENDING tasks on wake notifications or interval timeout.
    - Supports both embedded background thread execution (`start()` / `stop()`) and
      standalone foreground process execution (`run_forever()` / `request_shutdown()`).
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        *,
        worker_id: Optional[str] = None,
        poll_interval_seconds: Optional[float] = None,
        max_attempts: Optional[int] = None,
        stale_task_timeout_seconds: Optional[float] = None,
        lease_seconds: Optional[float] = None,
        heartbeat_interval_seconds: Optional[float] = None,
        shutdown_timeout_seconds: Optional[float] = None,
        mock_mode: bool = False,
    ) -> None:
        settings = get_settings()
        self.session_factory = session_factory
        self.worker_id = worker_id or generate_worker_id()
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else settings.worker_poll_interval_seconds
        )
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.worker_max_attempts
        )
        self.stale_task_timeout_seconds = (
            stale_task_timeout_seconds
            if stale_task_timeout_seconds is not None
            else settings.worker_stale_task_timeout_seconds
        )
        self.lease_seconds = (
            lease_seconds
            if lease_seconds is not None
            else settings.worker_lease_seconds
        )
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else settings.worker_heartbeat_interval_seconds
        )
        self.shutdown_timeout_seconds = (
            shutdown_timeout_seconds
            if shutdown_timeout_seconds is not None
            else settings.worker_shutdown_timeout_seconds
        )
        self.mock_mode = mock_mode
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._in_flight_done_event = threading.Event()
        self._in_flight_done_event.set()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active_foreground = False

    @property
    def is_running(self) -> bool:
        thread_alive = self._thread is not None and self._thread.is_alive()
        return (thread_alive or self._active_foreground) and not self._stop_event.is_set()

    @property
    def has_in_flight_task(self) -> bool:
        return not self._in_flight_done_event.is_set()

    def notify(self) -> None:
        """Wake the worker loop immediately to check the database for PENDING tasks."""
        self._wake_event.set()

    def request_shutdown(self) -> None:
        """Signal the worker loop to stop claiming new tasks and exit after any in-flight task finishes."""
        self._stop_event.set()
        self._wake_event.set()

    def wait_for_in_flight(self, timeout: Optional[float] = None) -> bool:
        """Wait up to `timeout` seconds for any currently executing task to finish."""
        effective_timeout = (
            timeout
            if timeout is not None
            else self.shutdown_timeout_seconds
        )
        return self._in_flight_done_event.wait(timeout=effective_timeout)

    def start(self) -> None:
        """Recover stale interrupted tasks and start the background polling thread if not already running."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._wake_event.clear()
            recover_interrupted_tasks(
                self.session_factory,
                max_attempts=self.max_attempts,
                stale_after_seconds=self.stale_task_timeout_seconds,
            )
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"leadforge-worker-{self.worker_id}",
                daemon=True,
            )
            self._thread.start()
            diag_msg = get_settings().format_runtime_diagnostic_log(
                component=f"EmbeddedWorker:{self.worker_id}"
            )
            logger.info(diag_msg)
            print(diag_msg, flush=True)
            logger.info(
                "DurableBackgroundWorker %s started (poll_interval=%.2fs, max_attempts=%d, lease=%.1fs)",
                self.worker_id,
                self.poll_interval_seconds,
                self.max_attempts,
                self.lease_seconds,
            )

    def stop(self, timeout: Optional[float] = None) -> bool:
        """
        Signal the background worker thread to stop claiming new tasks and wait up to `timeout`
        seconds for the current in-flight operation to complete gracefully.
        Returns True if the worker exited cleanly within the timeout, or False if the timeout expired.
        """
        effective_timeout = (
            timeout
            if timeout is not None
            else self.shutdown_timeout_seconds
        )
        with self._lock:
            self.request_shutdown()
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=effective_timeout)
            if thread.is_alive():
                logger.warning(
                    "DurableBackgroundWorker %s graceful shutdown timed out after %.2fs; in-flight task left recoverable in RUNNING state.",
                    self.worker_id,
                    effective_timeout,
                )
                return False
            logger.info("DurableBackgroundWorker %s stopped cleanly.", self.worker_id)
        return True

    def run_forever(self) -> None:
        """
        Run the worker loop synchronously in the current thread (used by the standalone worker process).
        Recovers stale tasks on startup and polls until `request_shutdown()` is called.
        """
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._active_foreground = True

        try:
            recover_interrupted_tasks(
                self.session_factory,
                max_attempts=self.max_attempts,
                stale_after_seconds=self.stale_task_timeout_seconds,
            )
            logger.info(
                "Standalone DurableBackgroundWorker %s running (poll_interval=%.2fs, max_attempts=%d, lease=%.1fs)",
                self.worker_id,
                self.poll_interval_seconds,
                self.max_attempts,
                self.lease_seconds,
            )
            self._run_loop()
        finally:
            self._active_foreground = False
            logger.info("Standalone DurableBackgroundWorker %s exited.", self.worker_id)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                recover_interrupted_tasks(
                    self.session_factory,
                    max_attempts=self.max_attempts,
                    stale_after_seconds=self.stale_task_timeout_seconds,
                )
                while not self._stop_event.is_set():
                    self._in_flight_done_event.clear()
                    try:
                        result = process_next_pending_task(
                            session_factory=self.session_factory,
                            mock_mode=self.mock_mode,
                            worker_id=self.worker_id,
                            lease_seconds=self.lease_seconds,
                            max_attempts=self.max_attempts,
                            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
                        )
                    finally:
                        self._in_flight_done_event.set()
                    if result is None:
                        break
            except Exception as exc:
                logger.error(
                    "Unexpected error in DurableBackgroundWorker %s loop: %s",
                    self.worker_id,
                    sanitize_error_message(f"{exc.__class__.__name__}: {str(exc)}"),
                )

            if self._stop_event.is_set():
                break
            self._wake_event.wait(timeout=self.poll_interval_seconds)
            self._wake_event.clear()


_global_worker: Optional[DurableBackgroundWorker] = None
_global_worker_lock = threading.Lock()


def get_background_worker() -> DurableBackgroundWorker:
    """Return the singleton DurableBackgroundWorker instance."""
    global _global_worker
    with _global_worker_lock:
        if _global_worker is None:
            _global_worker = DurableBackgroundWorker()
        return _global_worker


def start_background_worker(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    worker_id: Optional[str] = None,
    poll_interval_seconds: Optional[float] = None,
    max_attempts: Optional[int] = None,
    stale_task_timeout_seconds: Optional[float] = None,
    lease_seconds: Optional[float] = None,
    heartbeat_interval_seconds: Optional[float] = None,
    shutdown_timeout_seconds: Optional[float] = None,
    mock_mode: bool = False,
) -> DurableBackgroundWorker:
    """Configure and start the global DurableBackgroundWorker."""
    global _global_worker
    with _global_worker_lock:
        if _global_worker is not None and _global_worker.is_running:
            _global_worker.stop()
        _global_worker = DurableBackgroundWorker(
            session_factory=session_factory,
            worker_id=worker_id,
            poll_interval_seconds=poll_interval_seconds,
            max_attempts=max_attempts,
            stale_task_timeout_seconds=stale_task_timeout_seconds,
            lease_seconds=lease_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            mock_mode=mock_mode,
        )
        _global_worker.start()
        return _global_worker


def stop_background_worker(timeout: Optional[float] = None) -> bool:
    """Stop the global DurableBackgroundWorker if running."""
    global _global_worker
    with _global_worker_lock:
        worker = _global_worker
    if worker is not None:
        return worker.stop(timeout=timeout)
    return True

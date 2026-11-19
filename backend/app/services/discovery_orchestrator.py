"""
LeadForge Discovery & Qualification Pipeline Orchestrator.

Replaces the n8n workflow execution loop with a native Python pipeline:
1. Input validation & test-mode branch (Normalize Trigger Input, IF - Is Controlled Test Mode)
2. Business discovery via Apify (HTTP Request, Edit Fields, Code in JavaScript)
3. Website content retrieval (IF - Has Website, scraper)
4. AI business analysis & qualification via Gemini (Basic LLM Chain, Message a model, Code in JavaScript1)
5. Idempotent persistence & deterministic lead scoring via process_n8n_callback
"""

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db.base import utc_now
from backend.app.models.search_run import SearchRun, SearchRunStatus
from backend.app.schemas.n8n import (
    N8nCallbackRequest,
    N8nCallbackResponse,
    N8nLeadAnalysisPayload,
    N8nLeadItemPayload,
)
from backend.app.services.apify_service import (
    discover_businesses,
    scrape_website_content,
)
from backend.app.services.gemini_service import analyze_lead_with_gemini
from backend.app.services.n8n_service import process_n8n_callback

logger = logging.getLogger(__name__)


class DiscoveryPipelineResult(BaseModel):
    """Execution summary returned by the discovery pipeline."""

    search_run_id: int
    status: str
    city: str
    business_type: str
    max_results: int
    leads_discovered: int = Field(default=0)
    leads_received: int = Field(default=0)
    leads_created: int = Field(default=0)
    leads_updated: int = Field(default=0)
    leads_linked: int = Field(default=0)
    analyses_upserted: int = Field(default=0)
    test_mode: bool = Field(default=False)
    error_message: Optional[str] = None


def generate_controlled_test_lead(
    search_run_id: int,
    city: str,
    business_type: str,
) -> N8nLeadItemPayload:
    """
    Generate synthetic test lead matching n8n 'Build Controlled Test Lead'.
    Used when test_mode is active or city starts with 'TEST_ONLY_'.
    """
    place_id = f"TEST_PLACE_RUN_{search_run_id}"
    title = f"[TEST_ONLY] {business_type} ({city})"

    analysis = N8nLeadAnalysisPayload(
        ai_company_name=title,
        business_summary="Controlled test-mode synthesis (Apify, Gemini, and external scrapers bypassed).",
        personalization_observation="Verified Python pipeline execution and database persistence.",
        potential_opportunity="End-to-end orchestration verified without external side effects.",
        icebreaker="Controlled integration verification complete.",
        confidence=0.9,
    )

    raw_payload = {
        "apify": {
            "title": title,
            "placeId": place_id,
            "totalScore": 4.9,
            "reviewsCount": 42,
            "website": "https://test-only-verification.example.com",
            "address": f"100 Test Verification Way, {city}",
            "city": city,
            "categories": [business_type],
            "controlled_test_mode": True,
        },
        "stage1_ai": {
            "business_summary": "Controlled test-mode synthesis (Apify, Gemini, and external scrapers bypassed).",
            "website_status": "active",
            "confidence": "high",
        },
        "stage2_ai": {
            "company_name": title,
            "personalization_observation": "Verified Python pipeline execution and database persistence.",
            "potential_opportunity": "End-to-end orchestration verified without external side effects.",
            "icebreaker": "Controlled integration verification complete.",
        },
        "ai_company_name": title,
        "processing_status": "completed",
    }

    return N8nLeadItemPayload(
        company_name=title,
        title=title,
        place_id=place_id,
        placeId=place_id,
        website="https://test-only-verification.example.com",
        address=f"100 Test Verification Way, {city}",
        phone="+1-555-010-9999",
        city=city,
        business_type=business_type,
        categories=[business_type],
        rating=4.9,
        totalScore=4.9,
        reviews_count=42,
        reviewsCount=42,
        processing_status="completed",
        analysis=analysis,
        raw_payload=raw_payload,
    )


def execute_discovery_pipeline(
    db: Optional[Session],
    search_run_id: int,
    *,
    session_factory: Optional[Any] = None,
    mock_mode: bool = False,
    apify_items_override: Optional[list[dict[str, Any]]] = None,
    already_claimed: bool = False,
    expected_attempt_count: Optional[int] = None,
    expected_worker_id: Optional[str] = None,
    max_attempts: Optional[int] = None,
    heartbeat_callback: Optional[Any] = None,
) -> DiscoveryPipelineResult:
    """
    Execute the business discovery and AI qualification pipeline for a SearchRun.
    Translates the complete n8n workflow execution path into Python.

    - Uses short-lived database transactions: reads SearchRun inputs and closes/commits
      the read transaction BEFORE any external Apify, website scraper, or Gemini HTTP calls.
    - Persists leads incrementally after each business is analyzed so partial progress is not
      lost if a subsequent stage fails.
    - Enforces attempt and worker ownership fencing (`expected_attempt_count`, `expected_worker_id`)
      before persisting leads or updating SearchRun status.
    - Classifies runtime failures into permanent vs. transient errors and re-queues transient
      failures to PENDING when `attempt_count < max_attempts`.
    """
    from contextlib import contextmanager
    from backend.app.services.n8n_service import StaleTaskAttemptError
    from backend.app.services.worker_service import (
        is_permanent_task_error,
        sanitize_error_message,
    )

    @contextmanager
    def _short_session() -> Any:
        if session_factory is not None:
            with session_factory() as s:
                yield s
        elif db is not None:
            try:
                yield db
            finally:
                if db.in_transaction():
                    db.commit()
        else:
            from backend.app.db.session import SessionLocal

            with SessionLocal() as s:
                yield s

    effective_max_attempts = (
        max_attempts
        if max_attempts is not None
        else get_settings().worker_max_attempts
    )

    # 1. Short-lived read & claim transaction (closed/committed before network I/O)
    with _short_session() as read_session:
        search_run_obj = read_session.get(SearchRun, search_run_id)
        if search_run_obj is None:
            raise ValueError(f"SearchRun with id={search_run_id} not found in database.")

        city = (search_run_obj.city or "").strip()
        business_type = (search_run_obj.business_type or "").strip()
        max_results = search_run_obj.max_results or 20
        test_mode = bool(
            city.upper().startswith("TEST_ONLY_")
            or getattr(search_run_obj, "test_mode", False)
        )

        if not city:
            raise ValueError("SearchRun missing required field: city")
        if not business_type:
            raise ValueError("SearchRun missing required field: business_type")

        if not already_claimed:
            search_run_obj.status = SearchRunStatus.RUNNING.value
            search_run_obj.attempt_count = (search_run_obj.attempt_count or 0) + 1
            search_run_obj.started_at = utc_now()
            search_run_obj.completed_at = None
            search_run_obj.last_error = None
            search_run_obj.pipeline_stage = "discovering_businesses"
            read_session.add(search_run_obj)
            read_session.commit()
            read_session.refresh(search_run_obj)
            active_attempt = search_run_obj.attempt_count
            active_worker = search_run_obj.worker_id
        else:
            active_attempt = (
                expected_attempt_count
                if expected_attempt_count is not None
                else (search_run_obj.attempt_count or 1)
            )
            active_worker = (
                expected_worker_id
                if expected_worker_id is not None
                else search_run_obj.worker_id
            )
            search_run_obj.pipeline_stage = "discovering_businesses"
            read_session.add(search_run_obj)
            read_session.commit()

    fence_attempt = expected_attempt_count if expected_attempt_count is not None else active_attempt
    fence_worker = expected_worker_id if expected_worker_id is not None else active_worker

    def _set_stage(stage_name: str) -> None:
        with _short_session() as stage_session:
            sr = stage_session.get(SearchRun, search_run_id)
            if sr is None or sr.status != SearchRunStatus.RUNNING.value:
                return
            if fence_attempt is not None and (sr.attempt_count or 0) != fence_attempt:
                return
            if fence_worker is not None and sr.worker_id is not None and sr.worker_id != fence_worker:
                return
            sr.pipeline_stage = stage_name
            stage_session.add(sr)
            stage_session.commit()

    logger.info(
        "Starting discovery pipeline for SearchRun id=%d (attempt=%d, worker=%s): city='%s', business_type='%s', max_results=%d, test_mode=%s",
        search_run_id,
        active_attempt,
        fence_worker,
        city,
        business_type,
        max_results,
        test_mode,
    )

    try:
        if heartbeat_callback is not None:
            heartbeat_callback()

        # Branch 1: Controlled Test Mode
        if test_mode:
            _set_stage("analyzing_businesses")
            test_lead = generate_controlled_test_lead(search_run_id, city, business_type)
            _set_stage("saving_results")
            callback_req = N8nCallbackRequest(
                search_run_id=search_run_id,
                status="completed",
                leads=[test_lead],
            )
            with _short_session() as write_session:
                cb_resp: N8nCallbackResponse = process_n8n_callback(
                    write_session,
                    callback_req,
                    expected_attempt_count=fence_attempt,
                    expected_worker_id=fence_worker,
                    update_run_status=True,
                )
            return DiscoveryPipelineResult(
                search_run_id=search_run_id,
                status=cb_resp.status,
                city=city,
                business_type=business_type,
                max_results=max_results,
                leads_discovered=1,
                leads_received=cb_resp.leads_received,
                leads_created=cb_resp.leads_created,
                leads_updated=cb_resp.leads_updated,
                leads_linked=cb_resp.leads_linked,
                analyses_upserted=cb_resp.analyses_upserted,
                test_mode=True,
            )

        # Branch 2: Live Discovery (or injected override for testing) — NO DB TRANSACTION OPEN
        if apify_items_override is not None:
            discovered_items = apify_items_override
        else:
            discovered_items = discover_businesses(
                city=city,
                business_type=business_type,
                max_results=max_results,
            )

        if heartbeat_callback is not None:
            heartbeat_callback()

        # Handle empty batch
        if not discovered_items:
            logger.info("SearchRun id=%d returned 0 businesses from Apify.", search_run_id)
            _set_stage("saving_results")
            empty_callback = N8nCallbackRequest(
                search_run_id=search_run_id,
                status="completed",
                leads=[],
            )
            with _short_session() as write_session:
                cb_resp = process_n8n_callback(
                    write_session,
                    empty_callback,
                    expected_attempt_count=fence_attempt,
                    expected_worker_id=fence_worker,
                    update_run_status=True,
                )
            return DiscoveryPipelineResult(
                search_run_id=search_run_id,
                status=cb_resp.status,
                city=city,
                business_type=business_type,
                max_results=max_results,
                leads_discovered=0,
                leads_received=0,
                leads_created=0,
                leads_updated=0,
                leads_linked=0,
                analyses_upserted=0,
                test_mode=False,
            )

        scraper_timeout = get_settings().website_scraper_timeout_seconds
        total_received = 0
        total_created = 0
        total_updated = 0
        total_linked = 0
        total_analyses = 0

        # Process each discovered business through website scraping and Gemini qualification
        # outside of any database transaction, then persist each lead in a short-lived transaction.
        for idx, item in enumerate(discovered_items):
            _set_stage("analyzing_businesses")
            if heartbeat_callback is not None:
                heartbeat_callback()

            website = item.get("website")
            website_content = ""
            if website and isinstance(website, str) and website.strip():
                website_content = scrape_website_content(
                    website,
                    timeout_seconds=scraper_timeout,
                )

            if heartbeat_callback is not None:
                heartbeat_callback()

            analysis_payload, raw_ai, proc_status = analyze_lead_with_gemini(
                item,
                website_content=website_content,
                mock_mode=mock_mode,
            )

            raw_apify = dict(item.get("raw_apify") or {})
            combined_raw = {
                "apify": raw_apify,
                "stage1_ai": raw_ai.get("stage1_ai"),
                "stage2_ai": raw_ai.get("stage2_ai"),
                "ai_company_name": raw_ai.get("ai_company_name"),
                "processing_status": proc_status,
            }

            lead_item = N8nLeadItemPayload(
                company_name=item["title"],
                title=item["title"],
                place_id=item.get("place_id"),
                placeId=item.get("place_id"),
                website=item.get("website"),
                address=item.get("address"),
                phone=item.get("phone"),
                city=item.get("city") or city,
                business_type=item.get("business_type") or business_type,
                categories=item.get("categories") or [],
                rating=item.get("rating"),
                totalScore=item.get("rating"),
                reviews_count=item.get("reviews_count"),
                reviewsCount=item.get("reviews_count"),
                processing_status=proc_status,
                analysis=analysis_payload,
                raw_payload=combined_raw,
            )

            _set_stage("saving_results")
            is_last_item = idx == len(discovered_items) - 1
            incremental_req = N8nCallbackRequest(
                search_run_id=search_run_id,
                status="completed",
                leads=[lead_item],
            )
            with _short_session() as write_session:
                step_resp = process_n8n_callback(
                    write_session,
                    incremental_req,
                    expected_attempt_count=fence_attempt,
                    expected_worker_id=fence_worker,
                    update_run_status=is_last_item,
                )
            total_received += step_resp.leads_received
            total_created += step_resp.leads_created
            total_updated += step_resp.leads_updated
            total_linked += step_resp.leads_linked
            total_analyses += step_resp.analyses_upserted

        return DiscoveryPipelineResult(
            search_run_id=search_run_id,
            status=SearchRunStatus.COMPLETED.value,
            city=city,
            business_type=business_type,
            max_results=max_results,
            leads_discovered=len(discovered_items),
            leads_received=total_received,
            leads_created=total_created,
            leads_updated=total_updated,
            leads_linked=total_linked,
            analyses_upserted=total_analyses,
            test_mode=False,
        )

    except StaleTaskAttemptError as stale_exc:
        sanitized_stale = sanitize_error_message(str(stale_exc))
        logger.warning(
            "Rejected stale pipeline execution for SearchRun id=%d (attempt=%s, worker=%s): %s",
            search_run_id,
            fence_attempt,
            fence_worker,
            sanitized_stale,
        )
        return DiscoveryPipelineResult(
            search_run_id=search_run_id,
            status="stale",
            city=city,
            business_type=business_type,
            max_results=max_results,
            test_mode=test_mode,
            error_message=sanitized_stale,
        )

    except Exception as exc:
        sanitized_err = sanitize_error_message(
            f"{exc.__class__.__name__}: {str(exc)}"
        )
        is_permanent = is_permanent_task_error(exc)

        with _short_session() as err_session:
            current_run = err_session.get(SearchRun, search_run_id)
            if current_run is None:
                return DiscoveryPipelineResult(
                    search_run_id=search_run_id,
                    status=SearchRunStatus.FAILED.value,
                    city=city,
                    business_type=business_type,
                    max_results=max_results,
                    test_mode=test_mode,
                    error_message=sanitized_err,
                )

            # Enforce attempt and worker ownership fencing before mutating error status
            if current_run.status != SearchRunStatus.RUNNING.value:
                logger.warning(
                    "Stale failure update ignored for SearchRun id=%d: current status is %r (expected 'running')",
                    search_run_id,
                    current_run.status,
                )
                return DiscoveryPipelineResult(
                    search_run_id=search_run_id,
                    status="stale",
                    city=city,
                    business_type=business_type,
                    max_results=max_results,
                    test_mode=test_mode,
                    error_message=sanitized_err,
                )
            if (
                fence_attempt is not None
                and (current_run.attempt_count or 0) != fence_attempt
            ):
                logger.warning(
                    "Stale failure update ignored for SearchRun id=%d: current attempt_count is %d (expected %d)",
                    search_run_id,
                    current_run.attempt_count or 0,
                    fence_attempt,
                )
                return DiscoveryPipelineResult(
                    search_run_id=search_run_id,
                    status="stale",
                    city=city,
                    business_type=business_type,
                    max_results=max_results,
                    test_mode=test_mode,
                    error_message=sanitized_err,
                )
            if (
                fence_worker is not None
                and current_run.worker_id is not None
                and current_run.worker_id != fence_worker
            ):
                logger.warning(
                    "Stale failure update ignored for SearchRun id=%d: current worker_id is %r (expected %r)",
                    search_run_id,
                    current_run.worker_id,
                    fence_worker,
                )
                return DiscoveryPipelineResult(
                    search_run_id=search_run_id,
                    status="stale",
                    city=city,
                    business_type=business_type,
                    max_results=max_results,
                    test_mode=test_mode,
                    error_message=sanitized_err,
                )

            attempts_made = current_run.attempt_count or active_attempt or 1
            should_retry = (
                bool(already_claimed)
                and (not is_permanent)
                and (attempts_made < effective_max_attempts)
            )

            if should_retry:
                current_run.status = SearchRunStatus.PENDING.value
                current_run.completed_at = None
                current_run.last_error = sanitized_err
                current_run.worker_id = None
                current_run.lease_expires_at = None
                resulting_status = SearchRunStatus.PENDING.value
                logger.warning(
                    "Transient failure for SearchRun id=%d (attempt %d/%d) -> re-queued as PENDING: %s",
                    search_run_id,
                    attempts_made,
                    effective_max_attempts,
                    sanitized_err,
                )
            else:
                current_run.status = SearchRunStatus.FAILED.value
                current_run.completed_at = utc_now()
                current_run.last_error = sanitized_err
                current_run.worker_id = None
                current_run.lease_expires_at = None
                resulting_status = SearchRunStatus.FAILED.value
                logger.error(
                    "Terminal failure for SearchRun id=%d (attempt %d/%d, permanent=%s) -> FAILED: %s",
                    search_run_id,
                    attempts_made,
                    effective_max_attempts,
                    is_permanent,
                    sanitized_err,
                )

            err_session.add(current_run)
            err_session.commit()

        return DiscoveryPipelineResult(
            search_run_id=search_run_id,
            status=resulting_status,
            city=city,
            business_type=business_type,
            max_results=max_results,
            test_mode=test_mode,
            error_message=sanitized_err,
        )

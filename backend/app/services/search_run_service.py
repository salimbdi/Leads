from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.lead import Lead
from backend.app.models.search_run import SearchRun, SearchRunStatus
from backend.app.models.search_run_lead import SearchRunLead
from backend.app.schemas.lead import LeadSummaryResponse
from backend.app.schemas.search_run import (
    PaginatedSearchRunsResponse,
    SearchRunCreateRequest,
    SearchRunDetailResponse,
    SearchRunPipelineStatusResponse,
    SearchRunSummaryResponse,
)


from backend.app.config import get_settings
from backend.app.services.n8n_service import trigger_n8n_search_run
from backend.app.services.worker_service import enqueue_search_run_task


def _resolve_effective_pipeline_stage(run: SearchRun, leads_count: int) -> str:
    """Resolve the current factual pipeline execution stage for a SearchRun without fabricating status."""
    raw_stage = (getattr(run, "pipeline_stage", None) or "").strip()
    if run.status == SearchRunStatus.COMPLETED.value:
        return "completed"
    if run.status == SearchRunStatus.FAILED.value:
        if raw_stage and raw_stage not in {"completed", "waiting_in_queue", "search_submitted"}:
            return raw_stage
        return "failed"
    if run.status == SearchRunStatus.PENDING.value:
        return raw_stage if raw_stage in {"search_submitted", "waiting_in_queue"} else "waiting_in_queue"
    # RUNNING status
    if raw_stage and raw_stage not in {"search_submitted", "waiting_in_queue", "completed", "failed"}:
        return raw_stage
    return "saving_results" if leads_count > 0 else "discovering_businesses"


def list_search_runs_pipeline_status(
    db: Session,
    *,
    limit: int = 25,
) -> list[SearchRunPipelineStatusResponse]:
    """Return real-time pipeline execution stage and error details for recent SearchRuns."""
    stmt = (
        select(
            SearchRun,
            func.count(SearchRunLead.id).label("leads_count"),
        )
        .outerjoin(SearchRunLead, SearchRun.id == SearchRunLead.search_run_id)
        .group_by(SearchRun.id)
        .order_by(SearchRun.created_at.desc(), SearchRun.id.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        SearchRunPipelineStatusResponse(
            id=run.id,
            status=SearchRunStatus(run.status),
            pipeline_stage=_resolve_effective_pipeline_stage(run, int(leads_count or 0)),
            attempt_count=int(run.attempt_count or 0),
            leads_count=int(leads_count or 0),
            max_results=run.max_results,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            last_error=run.last_error,
            worker_id=run.worker_id,
        )
        for run, leads_count in rows
    ]


def create_search_run(db: Session, payload: SearchRunCreateRequest) -> SearchRunSummaryResponse:
    """
    Create and durably persist a new SearchRun before any background execution begins.
    - When discovery_execution_mode == 'python', enqueues the SearchRun for the durable Python worker (non-blocking).
    - Otherwise, dispatches to n8n if N8N_WEBHOOK_URL is configured.
    """
    search_run = SearchRun(
        city=payload.city,
        business_type=payload.business_type,
        max_results=payload.max_results,
        status=SearchRunStatus.PENDING.value,
        attempt_count=0,
        pipeline_stage="waiting_in_queue",
    )
    db.add(search_run)
    db.commit()
    db.refresh(search_run)

    settings = get_settings()
    if settings.discovery_execution_mode == "python":
        search_run = enqueue_search_run_task(db, search_run.id)
    else:
        # Trigger outbound n8n webhook if configured
        search_run = trigger_n8n_search_run(db, search_run)

    leads_count = int(
        db.scalar(
            select(func.count(SearchRunLead.id)).where(
                SearchRunLead.search_run_id == search_run.id
            )
        )
        or 0
    )

    return SearchRunSummaryResponse(
        id=search_run.id,
        city=search_run.city,
        business_type=search_run.business_type,
        max_results=search_run.max_results,
        status=SearchRunStatus(search_run.status),
        created_at=search_run.created_at,
        completed_at=search_run.completed_at,
        leads_count=leads_count,
    )


def dispatch_existing_search_run(
    db: Session,
    search_run_id: int,
) -> Optional[SearchRunSummaryResponse]:
    """
    Explicitly trigger or retry execution for an existing SearchRun.
    - When discovery_execution_mode == 'python', re-queues the task in PENDING state and wakes the Python worker.
    - Otherwise, dispatches to the configured n8n webhook.
    """
    search_run = db.scalar(select(SearchRun).where(SearchRun.id == search_run_id))
    if search_run is None:
        return None

    settings = get_settings()
    if settings.discovery_execution_mode == "python":
        search_run = enqueue_search_run_task(
            db,
            search_run.id,
            reset_if_failed=True,
        )
    else:
        search_run = trigger_n8n_search_run(db, search_run)

    leads_count = int(
        db.scalar(
            select(func.count(SearchRunLead.id)).where(
                SearchRunLead.search_run_id == search_run.id
            )
        )
        or 0
    )
    return SearchRunSummaryResponse(
        id=search_run.id,
        city=search_run.city,
        business_type=search_run.business_type,
        max_results=search_run.max_results,
        status=SearchRunStatus(search_run.status),
        created_at=search_run.created_at,
        completed_at=search_run.completed_at,
        leads_count=leads_count,
    )


def list_search_runs(
    db: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    status: Optional[SearchRunStatus] = None,
    city: Optional[str] = None,
    business_type: Optional[str] = None,
) -> PaginatedSearchRunsResponse:
    """List SearchRuns with pagination, optional filters, and aggregated lead counts (no N+1)."""
    conditions = []
    if status is not None:
        conditions.append(SearchRun.status == status.value)
    if city and city.strip():
        conditions.append(func.lower(SearchRun.city) == city.strip().lower())
    if business_type and business_type.strip():
        conditions.append(func.lower(SearchRun.business_type) == business_type.strip().lower())

    count_stmt = select(func.count(SearchRun.id)).where(*conditions)
    total: int = db.scalar(count_stmt) or 0

    stmt = (
        select(
            SearchRun,
            func.count(SearchRunLead.id).label("leads_count"),
        )
        .outerjoin(SearchRunLead, SearchRun.id == SearchRunLead.search_run_id)
        .where(*conditions)
        .group_by(SearchRun.id)
        .order_by(SearchRun.created_at.desc(), SearchRun.id.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = db.execute(stmt).all()
    items = [
        SearchRunSummaryResponse(
            id=run.id,
            city=run.city,
            business_type=run.business_type,
            max_results=run.max_results,
            status=SearchRunStatus(run.status),
            created_at=run.created_at,
            completed_at=run.completed_at,
            leads_count=int(leads_count or 0),
        )
        for run, leads_count in rows
    ]

    return PaginatedSearchRunsResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


def get_search_run_detail(
    db: Session,
    search_run_id: int,
) -> Optional[SearchRunDetailResponse]:
    """Retrieve a single SearchRun with its linked Leads and LeadAnalyses eagerly loaded."""
    stmt = (
        select(SearchRun)
        .where(SearchRun.id == search_run_id)
        .options(selectinload(SearchRun.leads).selectinload(Lead.analysis))
    )
    run = db.scalar(stmt)
    if run is None:
        return None

    sorted_leads = sorted(run.leads, key=lambda item: item.id)
    lead_summaries = [LeadSummaryResponse.model_validate(lead) for lead in sorted_leads]

    return SearchRunDetailResponse(
        id=run.id,
        city=run.city,
        business_type=run.business_type,
        max_results=run.max_results,
        status=SearchRunStatus(run.status),
        created_at=run.created_at,
        completed_at=run.completed_at,
        leads_count=len(lead_summaries),
        leads=lead_summaries,
    )

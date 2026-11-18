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
    SearchRunSummaryResponse,
)


from backend.app.services.n8n_service import trigger_n8n_search_run


def create_search_run(db: Session, payload: SearchRunCreateRequest) -> SearchRunSummaryResponse:
    """Create and persist a new SearchRun, and dispatch it to n8n if N8N_WEBHOOK_URL is configured."""
    search_run = SearchRun(
        city=payload.city,
        business_type=payload.business_type,
        max_results=payload.max_results,
        status=SearchRunStatus.PENDING.value,
    )
    db.add(search_run)
    db.commit()
    db.refresh(search_run)

    # Trigger outbound n8n webhook if configured
    search_run = trigger_n8n_search_run(db, search_run)
    leads_count = db.scalar(
        select(func.count(SearchRunLead.id)).where(
            SearchRunLead.search_run_id == search_run.id
        )
    ) or 0

    return SearchRunSummaryResponse(
        id=search_run.id,
        city=search_run.city,
        business_type=search_run.business_type,
        max_results=search_run.max_results,
        status=SearchRunStatus(search_run.status),
        created_at=search_run.created_at,
        completed_at=search_run.completed_at,
        leads_count=int(leads_count),
    )


def dispatch_existing_search_run(
    db: Session,
    search_run_id: int,
) -> Optional[SearchRunSummaryResponse]:
    """Explicitly trigger or retry outbound n8n webhook dispatch for an existing SearchRun."""
    search_run = db.scalar(select(SearchRun).where(SearchRun.id == search_run_id))
    if search_run is None:
        return None
    search_run = trigger_n8n_search_run(db, search_run)
    leads_count = db.scalar(
        select(func.count(SearchRunLead.id)).where(
            SearchRunLead.search_run_id == search_run.id
        )
    ) or 0
    return SearchRunSummaryResponse(
        id=search_run.id,
        city=search_run.city,
        business_type=search_run.business_type,
        max_results=search_run.max_results,
        status=SearchRunStatus(search_run.status),
        created_at=search_run.created_at,
        completed_at=search_run.completed_at,
        leads_count=int(leads_count),
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

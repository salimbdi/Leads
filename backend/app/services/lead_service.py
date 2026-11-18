from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.lead import Lead
from backend.app.models.search_run_lead import SearchRunLead
from backend.app.schemas.lead import (
    LeadDetailResponse,
    LeadSummaryResponse,
    PaginatedLeadsResponse,
    SearchRunBriefResponse,
)


def list_leads(
    db: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    city: Optional[str] = None,
    business_type: Optional[str] = None,
    search_run_id: Optional[int] = None,
    min_rating: Optional[float] = None,
    max_rating: Optional[float] = None,
    min_reviews: Optional[int] = None,
    min_score: Optional[int] = None,
    max_score: Optional[int] = None,
    priority: Optional[str] = None,
    has_website: Optional[bool] = None,
    has_phone: Optional[bool] = None,
    has_analysis: Optional[bool] = None,
    q: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> PaginatedLeadsResponse:
    """List Leads with pagination, rich filtering, deterministic score sorting, and eager-loaded LeadAnalysis."""
    conditions = []

    if city and city.strip():
        conditions.append(func.lower(Lead.city) == city.strip().lower())

    if business_type and business_type.strip():
        conditions.append(func.lower(Lead.business_type) == business_type.strip().lower())

    if search_run_id is not None:
        conditions.append(
            Lead.search_run_links.any(SearchRunLead.search_run_id == search_run_id)
        )

    if min_rating is not None:
        conditions.append(Lead.rating.is_not(None))
        conditions.append(Lead.rating >= min_rating)

    if max_rating is not None:
        conditions.append(Lead.rating.is_not(None))
        conditions.append(Lead.rating <= max_rating)

    if min_reviews is not None:
        conditions.append(Lead.reviews_count.is_not(None))
        conditions.append(Lead.reviews_count >= min_reviews)

    if min_score is not None:
        conditions.append(Lead.score >= min_score)

    if max_score is not None:
        conditions.append(Lead.score <= max_score)

    if priority and priority.strip():
        conditions.append(func.lower(Lead.priority) == priority.strip().lower())

    if has_website is True:
        conditions.append(Lead.website.is_not(None))
        conditions.append(func.length(func.trim(Lead.website)) > 0)
    elif has_website is False:
        conditions.append(
            or_(Lead.website.is_(None), func.length(func.trim(Lead.website)) == 0)
        )

    if has_phone is True:
        conditions.append(Lead.phone.is_not(None))
        conditions.append(func.length(func.trim(Lead.phone)) > 0)
    elif has_phone is False:
        conditions.append(
            or_(Lead.phone.is_(None), func.length(func.trim(Lead.phone)) == 0)
        )

    if has_analysis is True:
        conditions.append(Lead.analysis.has())
    elif has_analysis is False:
        conditions.append(~Lead.analysis.has())

    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(Lead.company_name.ilike(pattern))

    count_stmt = select(func.count(Lead.id)).where(*conditions)
    total: int = db.scalar(count_stmt) or 0

    sort_column_map = {
        "created_at": Lead.created_at,
        "score": Lead.score,
        "rating": Lead.rating,
        "reviews_count": Lead.reviews_count,
        "company_name": Lead.company_name,
    }
    target_col = sort_column_map.get(sort_by, Lead.created_at)
    is_asc = sort_order.lower() == "asc"

    if is_asc:
        order_clauses = [target_col.asc(), Lead.id.asc()]
    else:
        order_clauses = [target_col.desc(), Lead.id.desc()]

    stmt = (
        select(Lead)
        .where(*conditions)
        .options(selectinload(Lead.analysis))
        .order_by(*order_clauses)
        .limit(limit)
        .offset(offset)
    )

    leads = db.scalars(stmt).all()
    items = [LeadSummaryResponse.model_validate(lead) for lead in leads]

    return PaginatedLeadsResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


def get_lead_detail(db: Session, lead_id: int) -> Optional[LeadDetailResponse]:
    """Retrieve a single Lead with its LeadAnalysis and associated SearchRuns eagerly loaded."""
    stmt = (
        select(Lead)
        .where(Lead.id == lead_id)
        .options(
            selectinload(Lead.analysis),
            selectinload(Lead.search_runs),
        )
    )
    lead = db.scalar(stmt)
    if lead is None:
        return None

    sorted_runs = sorted(lead.search_runs, key=lambda r: r.id)
    summary = LeadSummaryResponse.model_validate(lead)

    return LeadDetailResponse(
        **summary.model_dump(),
        raw_payload=lead.raw_payload,
        search_runs=[SearchRunBriefResponse.model_validate(r) for r in sorted_runs],
    )


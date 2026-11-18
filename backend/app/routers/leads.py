from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.schemas.lead import LeadDetailResponse, PaginatedLeadsResponse
from backend.app.services.lead_service import get_lead_detail, list_leads

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])


@router.get(
    "",
    response_model=PaginatedLeadsResponse,
    summary="List Leads with pagination and filtering",
)
def list_leads_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="Maximum leads to return (1–100)"),
    offset: int = Query(default=0, ge=0, description="Number of leads to skip"),
    city: Optional[str] = Query(
        default=None,
        max_length=120,
        description="Case-insensitive filter by city",
    ),
    business_type: Optional[str] = Query(
        default=None,
        max_length=120,
        description="Case-insensitive filter by business type",
    ),
    search_run_id: Optional[int] = Query(
        default=None,
        gt=0,
        description="Filter leads associated with a specific SearchRun ID",
    ),
    min_rating: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=5.0,
        description="Minimum business rating (0.0–5.0)",
    ),
    max_rating: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=5.0,
        description="Maximum business rating (0.0–5.0)",
    ),
    min_reviews: Optional[int] = Query(
        default=None,
        ge=0,
        description="Minimum number of customer reviews",
    ),
    min_score: Optional[int] = Query(
        default=None,
        ge=0,
        le=100,
        description="Minimum deterministic lead score (0–100)",
    ),
    max_score: Optional[int] = Query(
        default=None,
        ge=0,
        le=100,
        description="Maximum deterministic lead score (0–100)",
    ),
    priority: Optional[str] = Query(
        default=None,
        max_length=16,
        description="Filter by deterministic priority (High, Medium, Low)",
    ),
    has_website: Optional[bool] = Query(
        default=None,
        description="Filter by presence of a non-empty website URL",
    ),
    has_phone: Optional[bool] = Query(
        default=None,
        description="Filter by presence of a non-empty phone number",
    ),
    has_analysis: Optional[bool] = Query(
        default=None,
        description="Filter by presence of AI-generated LeadAnalysis",
    ),
    q: Optional[str] = Query(
        default=None,
        max_length=255,
        description="Case-insensitive search substring on company_name",
    ),
    sort_by: Literal["created_at", "score", "rating", "reviews_count", "company_name"] = Query(
        default="created_at",
        description="Field to sort leads by",
    ),
    sort_order: Literal["asc", "desc"] = Query(
        default="desc",
        description="Sort direction (asc or desc)",
    ),
    db: Session = Depends(get_db),
) -> PaginatedLeadsResponse:
    """Retrieve a paginated, filtered list of business leads with eager-loaded analysis and deterministic scores."""
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="min_rating cannot be greater than max_rating",
        )

    if min_score is not None and max_score is not None and min_score > max_score:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="min_score cannot be greater than max_score",
        )

    if priority is not None and priority.strip():
        if priority.strip().lower() not in {"high", "medium", "low"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="priority must be one of: High, Medium, Low",
            )

    return list_leads(
        db,
        limit=limit,
        offset=offset,
        city=city,
        business_type=business_type,
        search_run_id=search_run_id,
        min_rating=min_rating,
        max_rating=max_rating,
        min_reviews=min_reviews,
        min_score=min_score,
        max_score=max_score,
        priority=priority,
        has_website=has_website,
        has_phone=has_phone,
        has_analysis=has_analysis,
        q=q,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/{lead_id}",
    response_model=LeadDetailResponse,
    summary="Retrieve a single Lead with analysis and search runs",
)
def get_lead_endpoint(
    lead_id: int = Path(..., gt=0, description="Unique Lead identifier"),
    db: Session = Depends(get_db),
) -> LeadDetailResponse:
    """Retrieve one Lead by ID with its LeadAnalysis and associated SearchRuns."""
    result = get_lead_detail(db, lead_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead with id {lead_id} not found",
        )
    return result

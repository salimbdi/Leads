from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models.search_run import SearchRunStatus
from backend.app.schemas.search_run import (
    PaginatedSearchRunsResponse,
    SearchRunCreateRequest,
    SearchRunDetailResponse,
    SearchRunSummaryResponse,
)
from backend.app.services.search_run_service import (
    create_search_run,
    dispatch_existing_search_run,
    get_search_run_detail,
    list_search_runs,
)

router = APIRouter(prefix="/api/v1/search-runs", tags=["search-runs"])


@router.post(
    "",
    response_model=SearchRunSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new SearchRun",
)
def create_search_run_endpoint(
    payload: SearchRunCreateRequest,
    db: Session = Depends(get_db),
) -> SearchRunSummaryResponse:
    """Create a new business discovery SearchRun record and trigger n8n if configured."""
    return create_search_run(db, payload)


@router.post(
    "/{search_run_id}/trigger",
    response_model=SearchRunSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger or retry n8n webhook dispatch for an existing SearchRun",
)
def trigger_search_run_endpoint(
    search_run_id: int = Path(..., gt=0, description="Unique SearchRun identifier"),
    db: Session = Depends(get_db),
) -> SearchRunSummaryResponse:
    """Explicitly dispatch an existing SearchRun to the configured n8n webhook."""
    result = dispatch_existing_search_run(db, search_run_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SearchRun with id {search_run_id} not found",
        )
    return result


@router.get(
    "",
    response_model=PaginatedSearchRunsResponse,
    summary="List SearchRuns with pagination and filtering",
)
def list_search_runs_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="Maximum items to return (1–100)"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    status_filter: Optional[SearchRunStatus] = Query(
        default=None,
        alias="status",
        description="Filter by execution status (pending, running, completed, failed)",
    ),
    city: Optional[str] = Query(
        default=None,
        max_length=120,
        description="Case-insensitive filter by city name",
    ),
    business_type: Optional[str] = Query(
        default=None,
        max_length=120,
        description="Case-insensitive filter by business type",
    ),
    db: Session = Depends(get_db),
) -> PaginatedSearchRunsResponse:
    """Retrieve a paginated list of SearchRuns with aggregated lead counts."""
    return list_search_runs(
        db,
        limit=limit,
        offset=offset,
        status=status_filter,
        city=city,
        business_type=business_type,
    )


@router.get(
    "/{search_run_id}",
    response_model=SearchRunDetailResponse,
    summary="Retrieve a SearchRun and its discovered leads",
)
def get_search_run_endpoint(
    search_run_id: int = Path(..., gt=0, description="Unique SearchRun identifier"),
    db: Session = Depends(get_db),
) -> SearchRunDetailResponse:
    """Retrieve a single SearchRun by ID including its associated leads and analyses."""
    result = get_search_run_detail(db, search_run_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SearchRun with id {search_run_id} not found",
        )
    return result


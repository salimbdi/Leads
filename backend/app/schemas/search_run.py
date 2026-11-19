from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.search_run import SearchRunStatus
from backend.app.schemas.lead import LeadSummaryResponse


class SearchRunCreateRequest(BaseModel):
    """Request schema for creating a new SearchRun."""

    model_config = ConfigDict(extra="forbid")

    city: str = Field(..., min_length=1, max_length=120, description="Target city for business discovery")
    business_type: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Category or type of business to discover",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Maximum number of business leads to discover (1–500)",
    )

    @field_validator("city", "business_type", mode="before")
    @classmethod
    def strip_and_validate_non_empty(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Must be a valid string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or whitespace only")
        return cleaned


class SearchRunSummaryResponse(BaseModel):
    """Response schema for a SearchRun in list views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    business_type: str
    max_results: int
    status: SearchRunStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    leads_count: int = Field(default=0, ge=0)


class SearchRunPipelineStatusResponse(BaseModel):
    """Detailed pipeline execution stage and error diagnostic response for a SearchRun."""

    id: int
    status: SearchRunStatus
    pipeline_stage: str
    attempt_count: int = Field(default=0, ge=0)
    leads_count: int = Field(default=0, ge=0)
    max_results: int = Field(default=20, ge=1)
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    worker_id: Optional[str] = None


class SearchRunDetailResponse(SearchRunSummaryResponse):
    """Detailed response schema for a single SearchRun including its discovered leads."""

    leads: list[LeadSummaryResponse] = Field(default_factory=list)


class PaginatedSearchRunsResponse(BaseModel):
    """Paginated response wrapper for SearchRun listings."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[SearchRunSummaryResponse]

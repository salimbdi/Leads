from datetime import datetime
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScoreDimensionBreakdown(BaseModel):
    """Structured explanation for a single lead scoring dimension."""

    score: int = Field(ge=0)
    max_score: int = Field(ge=1)
    reasons: list[str] = Field(default_factory=list)


class LeadScoreBreakdown(BaseModel):
    """Structured breakdown of all 5 deterministic lead scoring dimensions."""

    total_score: int = Field(ge=0, le=100)
    max_score: int = Field(default=100)
    priority: Literal["High", "Medium", "Low"]
    formula_version: str
    dimensions: dict[str, ScoreDimensionBreakdown]


class LeadAnalysisResponse(BaseModel):
    """Response schema for AI-generated qualitative lead analysis."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    business_summary: Optional[str] = None
    personalization_observation: Optional[str] = None
    potential_opportunity: Optional[str] = None
    icebreaker: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime
    updated_at: datetime


class SearchRunBriefResponse(BaseModel):
    """Compact SearchRun representation embedded inside Lead detail responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    business_type: str
    max_results: int
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None


class LeadSummaryResponse(BaseModel):
    """Response schema for a Lead item in paginated list views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_name: str
    website: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    city: str
    business_type: str
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    score: int = Field(default=0, ge=0, le=100)
    priority: Literal["High", "Medium", "Low"] = Field(default="Low")
    score_breakdown: LeadScoreBreakdown
    created_at: datetime
    updated_at: datetime
    analysis: Optional[LeadAnalysisResponse] = None

    @model_validator(mode="before")
    @classmethod
    def ensure_score_breakdown_populated(cls, data: Any) -> Any:
        from backend.app.services.scoring_service import calculate_lead_score, score_lead_model
        if isinstance(data, dict):
            raw_bd = data.get("score_breakdown")
            parsed_bd: Optional[dict[str, Any]] = None
            if isinstance(raw_bd, dict):
                parsed_bd = raw_bd
            elif isinstance(raw_bd, BaseModel):
                parsed_bd = raw_bd.model_dump()
            elif isinstance(raw_bd, str) and raw_bd.strip():
                try:
                    loaded = json.loads(raw_bd)
                    if isinstance(loaded, dict):
                        parsed_bd = loaded
                except ValueError:
                    parsed_bd = None

            if parsed_bd is None:
                analysis_obj = data.get("analysis")
                parsed_bd = calculate_lead_score(
                    company_name=data.get("company_name"),
                    website=data.get("website"),
                    address=data.get("address"),
                    phone=data.get("phone"),
                    city=data.get("city"),
                    business_type=data.get("business_type"),
                    rating=data.get("rating"),
                    reviews_count=data.get("reviews_count"),
                    raw_payload=data.get("raw_payload"),
                    business_summary=(
                        analysis_obj.get("business_summary")
                        if isinstance(analysis_obj, dict)
                        else getattr(analysis_obj, "business_summary", None)
                    ),
                    personalization_observation=(
                        analysis_obj.get("personalization_observation")
                        if isinstance(analysis_obj, dict)
                        else getattr(analysis_obj, "personalization_observation", None)
                    ),
                    potential_opportunity=(
                        analysis_obj.get("potential_opportunity")
                        if isinstance(analysis_obj, dict)
                        else getattr(analysis_obj, "potential_opportunity", None)
                    ),
                    icebreaker=(
                        analysis_obj.get("icebreaker")
                        if isinstance(analysis_obj, dict)
                        else getattr(analysis_obj, "icebreaker", None)
                    ),
                    confidence=(
                        analysis_obj.get("confidence")
                        if isinstance(analysis_obj, dict)
                        else getattr(analysis_obj, "confidence", None)
                    ),
                )
            out = dict(data)
            out["score"] = int(parsed_bd["total_score"])
            out["priority"] = str(parsed_bd["priority"])
            out["score_breakdown"] = parsed_bd
            return out

        # ORM Lead object
        raw_bd = getattr(data, "score_breakdown", None)
        parsed_bd = None
        if isinstance(raw_bd, str) and raw_bd.strip():
            try:
                loaded = json.loads(raw_bd)
                if isinstance(loaded, dict):
                    parsed_bd = loaded
            except ValueError:
                parsed_bd = None
        elif isinstance(raw_bd, dict):
            parsed_bd = raw_bd

        if parsed_bd is None:
            parsed_bd = score_lead_model(data)

        return {
            "id": getattr(data, "id"),
            "company_name": getattr(data, "company_name"),
            "website": getattr(data, "website", None),
            "address": getattr(data, "address", None),
            "phone": getattr(data, "phone", None),
            "city": getattr(data, "city"),
            "business_type": getattr(data, "business_type"),
            "rating": getattr(data, "rating", None),
            "reviews_count": getattr(data, "reviews_count", None),
            "score": int(parsed_bd["total_score"]),
            "priority": str(parsed_bd["priority"]),
            "score_breakdown": parsed_bd,
            "created_at": getattr(data, "created_at"),
            "updated_at": getattr(data, "updated_at"),
            "analysis": getattr(data, "analysis", None),
        }


class LeadDetailResponse(LeadSummaryResponse):
    """Detailed response schema for a single Lead, including analysis and associated search runs."""

    raw_payload: Optional[str] = None
    search_runs: list[SearchRunBriefResponse] = Field(default_factory=list)


class PaginatedLeadsResponse(BaseModel):
    """Paginated response wrapper for Lead listings."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[LeadSummaryResponse]


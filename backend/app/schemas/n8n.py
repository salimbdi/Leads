from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CONFIDENCE_LABEL_MAP: dict[str, float] = {
    "low": 0.3,
    "medium": 0.6,
    "high": 0.9,
}


def normalize_confidence_value(value: Any) -> Optional[float]:
    """Normalize string ('low'|'medium'|'high') or numeric confidence into a float in [0.0, 1.0]."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Confidence cannot be a boolean value.")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric < 0.0 or numeric > 1.0:
            raise ValueError("Numeric confidence must be between 0.0 and 1.0.")
        return round(numeric, 4)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if cleaned in CONFIDENCE_LABEL_MAP:
            return CONFIDENCE_LABEL_MAP[cleaned]
        try:
            numeric = float(cleaned)
        except ValueError as exc:
            raise ValueError(
                f"Invalid confidence string '{value}'. Expected 'low', 'medium', 'high', or a float in [0.0, 1.0]."
            ) from exc
        if numeric < 0.0 or numeric > 1.0:
            raise ValueError("Numeric confidence must be between 0.0 and 1.0.")
        return round(numeric, 4)
    raise ValueError("Confidence must be a string label or float between 0.0 and 1.0.")


class N8nWebhookOutboundPayload(BaseModel):
    """Outbound payload sent by FastAPI to the configured n8n Webhook Trigger."""

    model_config = ConfigDict(extra="forbid")

    search_run_id: int = Field(..., gt=0)
    city: str = Field(..., min_length=1, max_length=120)
    business_type: str = Field(..., min_length=1, max_length=120)
    max_results: int = Field(..., ge=1, le=500)
    callback_url: str = Field(..., min_length=1)


class N8nLeadAnalysisPayload(BaseModel):
    """Structured AI analysis payload for a single discovered business lead."""

    model_config = ConfigDict(extra="ignore")

    ai_company_name: Optional[str] = None
    business_summary: Optional[str] = None
    personalization_observation: Optional[str] = None
    potential_opportunity: Optional[str] = None
    icebreaker: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    stage1_analysis: Optional[dict[str, Any]] = None
    stage2_analysis: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @field_validator(
        "ai_company_name",
        "business_summary",
        "personalization_observation",
        "potential_opportunity",
        "icebreaker",
        "error",
        mode="before",
    )
    @classmethod
    def clean_optional_strings(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text if text else None

    @field_validator("confidence", mode="before")
    @classmethod
    def parse_confidence(cls, value: Any) -> Optional[float]:
        return normalize_confidence_value(value)

    def has_meaningful_content(self) -> bool:
        """Return True if at least one qualitative analysis field or confidence was generated."""
        return any(
            [
                self.business_summary,
                self.personalization_observation,
                self.potential_opportunity,
                self.icebreaker,
                self.confidence is not None,
            ]
        )


class N8nLeadItemPayload(BaseModel):
    """Single business lead item sent in the n8n batch callback."""

    model_config = ConfigDict(extra="ignore")

    company_name: Optional[str] = None
    title: Optional[str] = None
    place_id: Optional[str] = None
    placeId: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    business_type: Optional[str] = None
    categories: Optional[list[str]] = None
    rating: Optional[float] = Field(default=None, ge=0.0, le=5.0)
    totalScore: Optional[float] = Field(default=None, ge=0.0, le=5.0)
    reviews_count: Optional[int] = Field(default=None, ge=0)
    reviewsCount: Optional[int] = Field(default=None, ge=0)
    processing_status: str = Field(default="completed")
    analysis: Optional[N8nLeadAnalysisPayload] = None
    raw_payload: Optional[Union[dict[str, Any], str]] = None

    # Support flat AI fields if an upstream node emits flat item structures
    ai_company_name: Optional[str] = None
    business_summary: Optional[str] = None
    personalization_observation: Optional[str] = None
    potential_opportunity: Optional[str] = None
    icebreaker: Optional[str] = None
    confidence: Optional[Union[float, str]] = None
    stage1_ai: Optional[dict[str, Any]] = None
    stage2_ai: Optional[dict[str, Any]] = None

    @field_validator("rating", "totalScore", mode="before")
    @classmethod
    def normalize_rating(cls, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        numeric = float(value)
        if numeric < 0.0 or numeric > 5.0:
            raise ValueError("Rating must be between 0.0 and 5.0.")
        return numeric

    @field_validator("reviews_count", "reviewsCount", mode="before")
    @classmethod
    def normalize_reviews_count(cls, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        numeric = int(float(value))
        if numeric < 0:
            raise ValueError("Reviews count must be non-negative.")
        return numeric

    @model_validator(mode="after")
    def validate_and_consolidate_lead_fields(self) -> "N8nLeadItemPayload":
        # Determine canonical company name prioritizing original Apify title
        apify_title: Optional[str] = None
        if isinstance(self.raw_payload, dict):
            apify_obj = self.raw_payload.get("apify")
            if isinstance(apify_obj, dict) and apify_obj.get("title"):
                apify_title = str(apify_obj["title"]).strip()
            elif self.raw_payload.get("title"):
                apify_title = str(self.raw_payload["title"]).strip()

        canonical_name = (
            apify_title
            or (self.title.strip() if self.title and self.title.strip() else None)
            or (self.company_name.strip() if self.company_name and self.company_name.strip() else None)
        )
        if not canonical_name:
            raise ValueError("Lead item must include a non-empty Apify 'title' or 'company_name'.")
        self.company_name = canonical_name

        # Consolidate rating and reviews_count from Apify camelCase if needed
        if self.rating is None and self.totalScore is not None:
            self.rating = self.totalScore
        if self.reviews_count is None and self.reviewsCount is not None:
            self.reviews_count = self.reviewsCount

        # Consolidate place_id
        if not self.place_id and self.placeId:
            self.place_id = self.placeId.strip() or None

        # Consolidate flat AI fields into self.analysis if self.analysis was not explicitly provided
        if self.analysis is None and any(
            [
                self.business_summary,
                self.personalization_observation,
                self.potential_opportunity,
                self.icebreaker,
                self.confidence is not None,
                self.ai_company_name,
            ]
        ):
            self.analysis = N8nLeadAnalysisPayload(
                ai_company_name=self.ai_company_name,
                business_summary=self.business_summary,
                personalization_observation=self.personalization_observation,
                potential_opportunity=self.potential_opportunity,
                icebreaker=self.icebreaker,
                confidence=self.confidence,
                stage1_analysis=self.stage1_ai,
                stage2_analysis=self.stage2_ai,
            )

        return self


class N8nCallbackRequest(BaseModel):
    """Request schema for POST /api/v1/integrations/n8n/callback."""

    model_config = ConfigDict(extra="ignore")

    search_run_id: int = Field(..., gt=0)
    status: Literal["completed", "failed"] = "completed"
    error_message: Optional[str] = None
    execution_id: Optional[str] = None
    leads: list[N8nLeadItemPayload] = Field(default_factory=list)


class N8nCallbackResponse(BaseModel):
    """Response schema returned after processing an n8n batch callback."""

    search_run_id: int
    status: str
    leads_received: int = Field(ge=0)
    leads_created: int = Field(ge=0)
    leads_updated: int = Field(ge=0)
    leads_linked: int = Field(ge=0)
    analyses_upserted: int = Field(ge=0)

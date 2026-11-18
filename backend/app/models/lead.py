from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, utc_now

if TYPE_CHECKING:
    from backend.app.models.lead_analysis import LeadAnalysis
    from backend.app.models.search_run import SearchRun
    from backend.app.models.search_run_lead import SearchRunLead


class Lead(Base):
    """Represents a discovered business lead and its normalized core attributes."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "length(trim(company_name)) > 0",
            name="ck_leads_company_name_non_empty",
        ),
        CheckConstraint(
            "length(trim(city)) > 0",
            name="ck_leads_city_non_empty",
        ),
        CheckConstraint(
            "length(trim(business_type)) > 0",
            name="ck_leads_business_type_non_empty",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 0.0 AND rating <= 5.0)",
            name="ck_leads_rating_range",
        ),
        CheckConstraint(
            "reviews_count IS NULL OR reviews_count >= 0",
            name="ck_leads_reviews_count_non_negative",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_leads_score_range",
        ),
        CheckConstraint(
            "priority IN ('High', 'Medium', 'Low')",
            name="ck_leads_priority_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    business_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="Low", index=True)
    score_breakdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    # One-to-one relationship to LeadAnalysis
    analysis: Mapped[Optional["LeadAnalysis"]] = relationship(
        "LeadAnalysis",
        back_populates="lead",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Explicit association object links to SearchRun
    search_run_links: Mapped[list["SearchRunLead"]] = relationship(
        "SearchRunLead",
        back_populates="lead",
        cascade="all, delete-orphan",
    )

    # Convenience many-to-many read relationship to SearchRun
    search_runs: Mapped[list["SearchRun"]] = relationship(
        "SearchRun",
        secondary="search_run_leads",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Lead(id={self.id}, company_name={self.company_name!r}, "
            f"city={self.city!r}, business_type={self.business_type!r})>"
        )

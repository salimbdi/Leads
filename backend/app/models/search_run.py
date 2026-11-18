from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, utc_now

if TYPE_CHECKING:
    from backend.app.models.lead import Lead
    from backend.app.models.search_run_lead import SearchRunLead


class SearchRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SearchRun(Base):
    """Represents a business discovery search execution run."""

    __tablename__ = "search_runs"
    __table_args__ = (
        CheckConstraint(
            "length(trim(city)) > 0",
            name="ck_search_runs_city_non_empty",
        ),
        CheckConstraint(
            "length(trim(business_type)) > 0",
            name="ck_search_runs_business_type_non_empty",
        ),
        CheckConstraint(
            "max_results > 0 AND max_results <= 500",
            name="ck_search_runs_max_results_range",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_search_runs_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    business_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SearchRunStatus.PENDING.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # Explicit association object relationship
    search_run_leads: Mapped[list["SearchRunLead"]] = relationship(
        "SearchRunLead",
        back_populates="search_run",
        cascade="all, delete-orphan",
    )

    # Convenience many-to-many read relationship to Lead via search_run_leads
    leads: Mapped[list["Lead"]] = relationship(
        "Lead",
        secondary="search_run_leads",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return (
            f"<SearchRun(id={self.id}, city={self.city!r}, "
            f"business_type={self.business_type!r}, status={self.status!r})>"
        )

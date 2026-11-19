from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
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
    """Represents a business discovery search execution run and durable task lifecycle record."""

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
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_search_runs_attempt_count_non_negative",
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
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    worker_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        default=None,
    )
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    pipeline_stage: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        default="waiting_in_queue",
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

    @property
    def task_id(self) -> int:
        """Durable task identifier for this SearchRun."""
        return self.id

    @property
    def search_run_id(self) -> int:
        """Associated SearchRun ID for durable task tracking."""
        return self.id

    @property
    def error_message(self) -> Optional[str]:
        """Alias for last_error."""
        return self.last_error

    @error_message.setter
    def error_message(self, value: Optional[str]) -> None:
        self.last_error = value

    def __repr__(self) -> str:
        return (
            f"<SearchRun(id={self.id}, city={self.city!r}, "
            f"business_type={self.business_type!r}, status={self.status!r}, "
            f"attempt_count={self.attempt_count})>"
        )

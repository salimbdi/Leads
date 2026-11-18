from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, utc_now

if TYPE_CHECKING:
    from backend.app.models.lead import Lead
    from backend.app.models.search_run import SearchRun


class SearchRunLead(Base):
    """Association table linking SearchRuns and Leads with uniqueness enforcement."""

    __tablename__ = "search_run_leads"
    __table_args__ = (
        UniqueConstraint(
            "search_run_id",
            "lead_id",
            name="uq_search_run_leads_run_lead",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    search_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("search_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    search_run: Mapped["SearchRun"] = relationship(
        "SearchRun",
        back_populates="search_run_leads",
    )
    lead: Mapped["Lead"] = relationship(
        "Lead",
        back_populates="search_run_links",
    )

    def __repr__(self) -> str:
        return (
            f"<SearchRunLead(id={self.id}, search_run_id={self.search_run_id}, "
            f"lead_id={self.lead_id})>"
        )

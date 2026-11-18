from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, utc_now

if TYPE_CHECKING:
    from backend.app.models.lead import Lead


class LeadAnalysis(Base):
    """Stores structured AI-generated qualitative analysis and evidence for a Lead."""

    __tablename__ = "lead_analyses"
    __table_args__ = (
        UniqueConstraint("lead_id", name="uq_lead_analyses_lead_id"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_lead_analyses_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    lead_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    personalization_observation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    potential_opportunity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icebreaker: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
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

    lead: Mapped["Lead"] = relationship("Lead", back_populates="analysis")

    def __repr__(self) -> str:
        return f"<LeadAnalysis(id={self.id}, lead_id={self.lead_id}, confidence={self.confidence})>"

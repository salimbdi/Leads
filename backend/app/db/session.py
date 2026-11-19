from collections.abc import Generator
from typing import Any, Optional

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.db.base import Base


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    """Ensure SQLite enforces foreign keys and safe concurrency settings (WAL, NORMAL sync, busy_timeout)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_sqlite_engine(database_url: Optional[str] = None) -> Engine:
    """Create a configured SQLAlchemy Engine, applying SQLite PRAGMAs only to SQLite databases."""
    settings = get_settings()
    resolved_url = database_url or settings.database_url
    if resolved_url == "sqlite:///./leadforge.db":
        resolved_url = f"sqlite:///{settings.get_resolved_database_path()}"
    connect_args: dict[str, Any] = {}
    if resolved_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    new_engine = create_engine(
        resolved_url,
        connect_args=connect_args,
        future=True,
    )

    if new_engine.dialect.name == "sqlite":
        event.listen(new_engine, "connect", _enable_sqlite_foreign_keys)

    return new_engine


engine: Engine = create_sqlite_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def _migrate_and_backfill_sqlite_scoring_columns(active_engine: Engine) -> None:
    """
    Safely add Phase 6 scoring columns (`score`, `priority`, `score_breakdown`) to
    existing SQLite `leads` tables without dropping or resetting existing records,
    and backfill deterministic scores for any pre-existing rows.
    """
    if active_engine.dialect.name != "sqlite":
        return

    inspector = inspect(active_engine)
    if "leads" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("leads")}
    with active_engine.begin() as conn:
        if "score" not in existing_cols:
            conn.execute(
                text("ALTER TABLE leads ADD COLUMN score INTEGER NOT NULL DEFAULT 0")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_leads_score ON leads (score)")
            )
        if "priority" not in existing_cols:
            conn.execute(
                text("ALTER TABLE leads ADD COLUMN priority VARCHAR(16) NOT NULL DEFAULT 'Low'")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_leads_priority ON leads (priority)")
            )
        if "score_breakdown" not in existing_cols:
            conn.execute(
                text("ALTER TABLE leads ADD COLUMN score_breakdown TEXT")
            )

    # Backfill any pre-existing Lead records that lack a computed score_breakdown
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from backend.app.models.lead import Lead
    from backend.app.services.scoring_service import apply_lead_scoring

    with Session(bind=active_engine) as backfill_session:
        unscored_leads = backfill_session.scalars(
            select(Lead)
            .where(Lead.score_breakdown.is_(None))
            .options(selectinload(Lead.analysis))
        ).all()
        if unscored_leads:
            for lead in unscored_leads:
                apply_lead_scoring(lead)
            backfill_session.commit()


def _auto_score_leads_before_flush(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    """Ensure any new or updated Lead (or LeadAnalysis) updates deterministic Lead scoring without premature lazy loads."""
    from sqlalchemy.orm.base import NO_VALUE
    from backend.app.models.lead import Lead
    from backend.app.models.lead_analysis import LeadAnalysis
    from backend.app.services.scoring_service import apply_lead_scoring

    # Map pending LeadAnalysis objects by lead_id or Lead reference
    pending_analyses_by_lead_id: dict[int, LeadAnalysis] = {}
    pending_analyses_by_lead_obj: dict[int, LeadAnalysis] = {}

    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, LeadAnalysis) and obj not in session.deleted:
            if obj.confidence is not None and (obj.confidence < 0.0 or obj.confidence > 1.0):
                continue
            if obj.lead_id is not None:
                pending_analyses_by_lead_id[obj.lead_id] = obj
            loaded_lead = inspect(obj).attrs.lead.loaded_value
            if loaded_lead is not NO_VALUE and loaded_lead is not None:
                pending_analyses_by_lead_obj[id(loaded_lead)] = obj

    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Lead) and obj not in session.deleted:
            loaded_analysis = inspect(obj).attrs.analysis.loaded_value
            analysis_to_use = None
            if loaded_analysis is not NO_VALUE and loaded_analysis is not None:
                analysis_to_use = loaded_analysis
            elif id(obj) in pending_analyses_by_lead_obj:
                analysis_to_use = pending_analyses_by_lead_obj[id(obj)]
            elif obj.id is not None and obj.id in pending_analyses_by_lead_id:
                analysis_to_use = pending_analyses_by_lead_id[obj.id]
            apply_lead_scoring(obj, analysis_override=analysis_to_use, allow_lazy_analysis=False)

    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, LeadAnalysis) and obj not in session.deleted:
            if obj.confidence is not None and (obj.confidence < 0.0 or obj.confidence > 1.0):
                continue
            loaded_lead = inspect(obj).attrs.lead.loaded_value
            parent_lead = loaded_lead if loaded_lead is not NO_VALUE else None
            if parent_lead is None and obj.lead_id is not None:
                identity_key = session.identity_key(Lead, (obj.lead_id,))
                parent_lead = session.identity_map.get(identity_key)
            if parent_lead is not None and parent_lead not in session.deleted:
                loaded_existing = inspect(parent_lead).attrs.analysis.loaded_value
                if loaded_existing is NO_VALUE or loaded_existing is None or loaded_existing is obj:
                    apply_lead_scoring(
                        parent_lead,
                        analysis_override=obj,
                        allow_lazy_analysis=False,
                    )


event.listen(Session, "before_flush", _auto_score_leads_before_flush)


def _migrate_sqlite_search_run_worker_columns(active_engine: Engine) -> None:
    """
    Safely add Phase 2 durable worker lifecycle columns (`attempt_count`, `started_at`, `last_error`,
    `worker_id`, `lease_expires_at`) to existing SQLite `search_runs` tables without dropping or
    resetting existing records.
    """
    if active_engine.dialect.name != "sqlite":
        return

    inspector = inspect(active_engine)
    if "search_runs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("search_runs")}
    with active_engine.begin() as conn:
        if "attempt_count" not in existing_cols:
            conn.execute(
                text(
                    "ALTER TABLE search_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "started_at" not in existing_cols:
            conn.execute(
                text("ALTER TABLE search_runs ADD COLUMN started_at DATETIME")
            )
        if "last_error" not in existing_cols:
            conn.execute(
                text("ALTER TABLE search_runs ADD COLUMN last_error TEXT")
            )
        if "worker_id" not in existing_cols:
            conn.execute(
                text("ALTER TABLE search_runs ADD COLUMN worker_id VARCHAR(128)")
            )
        if "lease_expires_at" not in existing_cols:
            conn.execute(
                text("ALTER TABLE search_runs ADD COLUMN lease_expires_at DATETIME")
            )
        if "pipeline_stage" not in existing_cols:
            conn.execute(
                text("ALTER TABLE search_runs ADD COLUMN pipeline_stage VARCHAR(64)")
            )


def init_db(target_engine: Optional[Engine] = None) -> None:
    """Import all models, initialize database tables, and run safe additive migrations."""
    # Ensure all models are registered on Base.metadata before create_all
    import backend.app.models  # noqa: F401

    active_engine = target_engine or engine
    Base.metadata.create_all(bind=active_engine)
    _migrate_sqlite_search_run_worker_columns(active_engine)
    _migrate_and_backfill_sqlite_scoring_columns(active_engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a managed SQLAlchemy database session."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def verify_database_connection(target_engine: Optional[Engine] = None) -> dict[str, Any]:
    """Verify SQLite connectivity, foreign key enforcement, and registered tables."""
    active_engine = target_engine or engine
    init_db(active_engine)
    with active_engine.connect() as conn:
        fk_enabled = conn.execute(text("PRAGMA foreign_keys")).scalar()
    inspector = inspect(active_engine)
    tables = sorted(inspector.get_table_names())
    return {
        "connected": True,
        "foreign_keys_enforced": bool(fk_enabled),
        "tables": tables,
        "table_count": len(tables),
    }


def get_schema_metadata(target_engine: Optional[Engine] = None) -> dict[str, Any]:
    """Inspect and return detailed schema metadata for all SQLite tables."""
    active_engine = target_engine or engine
    init_db(active_engine)
    inspector = inspect(active_engine)
    table_names = sorted(inspector.get_table_names())
    tables_info: list[dict[str, Any]] = []

    for table_name in table_names:
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk_cols = set(pk_constraint.get("constrained_columns", []))
        fks = inspector.get_foreign_keys(table_name)
        uqs = inspector.get_unique_constraints(table_name)
        cks = inspector.get_check_constraints(table_name)
        indexes = inspector.get_indexes(table_name)

        columns_info = []
        for col in inspector.get_columns(table_name):
            columns_info.append(
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": bool(col["nullable"]),
                    "primary_key": col["name"] in pk_cols,
                }
            )

        tables_info.append(
            {
                "table_name": table_name,
                "columns": columns_info,
                "foreign_keys": [
                    {
                        "constrained_columns": fk["constrained_columns"],
                        "referred_table": fk["referred_table"],
                        "referred_columns": fk["referred_columns"],
                        "ondelete": fk.get("options", {}).get("ondelete"),
                    }
                    for fk in fks
                ],
                "unique_constraints": [
                    {"name": uq.get("name"), "columns": uq.get("column_names", [])}
                    for uq in uqs
                ],
                "check_constraints": [
                    {"name": ck.get("name"), "sqltext": ck.get("sqltext")}
                    for ck in cks
                ],
                "indexes": [
                    {
                        "name": idx.get("name"),
                        "columns": idx.get("column_names", []),
                        "unique": bool(idx.get("unique")),
                    }
                    for idx in indexes
                ],
            }
        )

    return {
        "dialect": active_engine.dialect.name,
        "table_count": len(tables_info),
        "tables": tables_info,
    }

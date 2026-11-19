"""
Standalone LeadForge Durable Python Background Worker Entry Point.

Runs the database-backed LeadForge background worker as an independent OS process,
completely decoupled from the FastAPI HTTP server process.

Usage:
    python -m backend.app.worker
    python -m backend.app.worker --once
    python -m backend.app.worker --poll-interval 2.0 --max-attempts 3 --lease-seconds 180
"""

import argparse
import logging
import signal
import sys
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.db.session import create_sqlite_engine, init_db
from backend.app.services.worker_service import (
    DurableBackgroundWorker,
    drain_pending_tasks,
    generate_worker_id,
    recover_interrupted_tasks,
)

logger = logging.getLogger("backend.app.worker")


def build_worker_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.worker",
        description="LeadForge standalone durable Python background worker process.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional SQLAlchemy database URL override (defaults to settings.database_url).",
    )
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Optional unique worker instance identifier.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="Database polling interval in seconds (defaults to WORKER_POLL_INTERVAL_SECONDS).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Maximum execution attempts per SearchRun task (defaults to WORKER_MAX_ATTEMPTS).",
    )
    parser.add_argument(
        "--stale-timeout",
        type=float,
        default=None,
        help="Stale RUNNING task recovery threshold in seconds (defaults to WORKER_STALE_TASK_TIMEOUT_SECONDS).",
    )
    parser.add_argument(
        "--lease-seconds",
        type=float,
        default=None,
        help="Worker lease duration in seconds (defaults to WORKER_LEASE_SECONDS).",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=None,
        help="Lease renewal heartbeat interval in seconds (defaults to WORKER_HEARTBEAT_INTERVAL_SECONDS).",
    )
    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=None,
        help="Graceful shutdown wait period in seconds (defaults to WORKER_SHUTDOWN_TIMEOUT_SECONDS).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Recover stale tasks, drain currently pending tasks once, and exit.",
    )
    parser.add_argument(
        "--mock-mode",
        action="store_true",
        help="Execute Gemini analysis in explicit mock mode (for automated testing).",
    )
    return parser


def run_standalone_worker(argv: Optional[Sequence[str]] = None) -> int:
    """
    Initialize the LeadForge database with an isolated SQLAlchemy engine/sessionmaker,
    register SIGINT and SIGTERM signal handlers for graceful shutdown, recover stale
    interrupted tasks, and execute the worker loop independently of FastAPI.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = build_worker_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    settings = get_settings()
    diag_msg = settings.format_runtime_diagnostic_log(component="StandaloneWorker")
    logger.info(diag_msg)
    print(diag_msg, flush=True)
    resolved_db_url = args.database_url or settings.database_url
    worker_id = args.worker_id or generate_worker_id(prefix="standalone-worker")
    use_mock = bool(args.mock_mode or settings.ai_provider == "mock")

    worker_engine = create_sqlite_engine(resolved_db_url)
    init_db(worker_engine)

    WorkerSessionLocal = sessionmaker(
        bind=worker_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    try:
        if args.once:
            recover_interrupted_tasks(
                WorkerSessionLocal,
                max_attempts=args.max_attempts,
                stale_after_seconds=args.stale_timeout,
            )
            drain_pending_tasks(
                WorkerSessionLocal,
                mock_mode=use_mock,
                worker_id=worker_id,
                lease_seconds=args.lease_seconds,
                max_attempts=args.max_attempts,
            )
            return 0

        worker = DurableBackgroundWorker(
            session_factory=WorkerSessionLocal,
            worker_id=worker_id,
            poll_interval_seconds=args.poll_interval,
            max_attempts=args.max_attempts,
            stale_task_timeout_seconds=args.stale_timeout,
            lease_seconds=args.lease_seconds,
            heartbeat_interval_seconds=args.heartbeat_interval,
            shutdown_timeout_seconds=args.shutdown_timeout,
            mock_mode=use_mock,
        )

        previous_sigint = signal.getsignal(signal.SIGINT)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def _handle_shutdown_signal(signum: int, _frame: Any) -> None:
            sig_name = (
                signal.Signals(signum).name
                if signum in signal.Signals._value2member_map_
                else str(signum)
            )
            logger.info(
                "Received %s on standalone worker %s; stopping new task claims and waiting up to %.1fs for in-flight work...",
                sig_name,
                worker.worker_id,
                worker.shutdown_timeout_seconds,
            )
            worker.request_shutdown()

        signal.signal(signal.SIGINT, _handle_shutdown_signal)
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)

        try:
            worker.start()
            while worker.is_running:
                worker._stop_event.wait(timeout=0.2)
            clean_exit = worker.stop(timeout=worker.shutdown_timeout_seconds)
            return 0 if clean_exit else 1
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)
    finally:
        worker_engine.dispose()


if __name__ == "__main__":
    sys.exit(run_standalone_worker())

from functools import lru_cache
import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional, Union
from urllib.parse import urlsplit
from dotenv import dotenv_values
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
PROJECT_ENV_FILE: Path = PROJECT_ROOT / ".env"
PROJECT_ENV_EXAMPLE_FILE: Path = PROJECT_ROOT / ".env.example"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
_RECOGNIZED_DOCKER_WEBHOOK_HOSTS = {
    "n8n",
    "leadforge-n8n",
    "leadforge_n8n",
}
_RECOGNIZED_DOCKER_CALLBACK_HOSTS = {
    "leadforge-app",
    "leadforge_app",
    "backend",
    "leadforge-backend",
    "leadforge_backend",
    "gateway",
    "leadforge-gateway",
    "leadforge_gateway",
    "api",
    "leadforge-api",
    "leadforge_api",
}
_PLACEHOLDER_MARKERS = (
    "my_app_url",
    "my_backend",
    "your_",
    "your-",
    "<",
    ">",
    "placeholder",
    "changeme",
    "todo",
    "example.invalid",
)


def _is_secret_placeholder(val: Optional[str]) -> bool:
    if not val or not val.strip():
        return True
    cleaned = val.strip().strip("\"'").strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    if lowered in {
        "my_gemini_api_key",
        "your_gemini_api_key",
        "your_apify_api_token",
        "my_apify_api_token",
        "changeme",
        "placeholder",
    }:
        return True
    return False


def read_project_dotenv_values() -> dict[str, str]:
    """
    Read the canonical project-root `.env` file (`PROJECT_ROOT / ".env"`).
    If any key is missing or empty in `.env` (for example after an AI Studio container
    reload where `.env` was reset while `.env.example` retained the user's configured
    settings), fall back to non-placeholder values from `PROJECT_ROOT / ".env.example"`.
    """
    merged: dict[str, str] = {}
    if PROJECT_ENV_EXAMPLE_FILE.exists():
        try:
            for k, v in dotenv_values(PROJECT_ENV_EXAMPLE_FILE).items():
                if k and v is not None and str(v).strip():
                    merged[k] = str(v).strip()
        except Exception:
            pass

    if PROJECT_ENV_FILE.exists():
        try:
            for k, v in dotenv_values(PROJECT_ENV_FILE).items():
                if k and v is not None and str(v).strip():
                    # Do not let an old template 'mock' or 'gemini-4-carbon' in a reset .env
                    # override a valid gemini configuration when GEMINI_API_KEY is configured
                    val_str = str(v).strip()
                    if (
                        k.upper() == "AI_PROVIDER"
                        and val_str.lower() == "mock"
                        and merged.get("AI_PROVIDER", "").lower() == "gemini"
                    ):
                        continue
                    if (
                        k.upper() == "AI_MODEL"
                        and val_str.lower() == "gemini-4-carbon"
                        and merged.get("AI_MODEL", "")
                    ):
                        continue
                    merged[k] = val_str
        except Exception:
            pass

    return merged


def is_loopback_url(url: str) -> bool:
    """Return True if the URL hostname is a loopback or local-only address."""
    if not url or not url.strip():
        return False
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        return host in _LOOPBACK_HOSTS
    except ValueError:
        return False


def is_recognized_docker_service_url(url: str, allowed_hosts: set[str]) -> bool:
    """Return True if the URL targets an explicitly recognized internal Docker Compose service hostname."""
    if not url or not url.strip() or is_loopback_url(url):
        return False
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        return bool(host and host in allowed_hosts)
    except ValueError:
        return False


def is_placeholder_url(url: Optional[str]) -> bool:
    """Return True if the URL string is a template placeholder rather than a real destination."""
    if not url or not url.strip():
        return False
    lowered = url.strip().lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return True
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        if host in {"your-cloud-run-url.run.app", "your-n8n-tunnel.trycloudflare.com"}:
            return True
    except ValueError:
        return True
    return False


def is_session_gated_preview_url(url: str) -> bool:
    """Return True if the URL points to a session-gated AI Studio preview proxy."""
    if not url or not url.strip():
        return False
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        return (
            host.startswith("ais-dev-") or host.startswith("ais-pre-")
        ) and host.endswith(".run.app")
    except ValueError:
        return False


class Settings(BaseSettings):
    """Application settings loaded from the canonical project-root .env file and environment."""

    app_name: str = "LeadForge"
    app_env: str = "development"
    app_version: str = "0.1.0"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8001

    # Database configuration (models implemented in Phase 2)
    database_url: str = "sqlite:///./leadforge.db"
    sqlite_durable_mount_path: str = ""

    # AI analysis provider configuration
    ai_provider: Literal["mock", "gemini"] = "gemini"
    ai_model: str = "gemini-3.1-flash-lite"
    gemini_api_key: str = ""

    # Apify business discovery configuration (Python pipeline)
    apify_api_token: str = ""
    apify_actor_id: str = "compass~google-maps-extractor"
    apify_request_timeout_seconds: float = 60.0
    website_scraper_timeout_seconds: float = 15.0

    # Durable Python background worker configuration (Phase 2)
    discovery_execution_mode: Literal["n8n", "python"] = "python"
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = 2.0
    worker_max_attempts: int = 3
    worker_stale_task_timeout_seconds: float = 180.0
    worker_lease_seconds: float = 180.0
    worker_heartbeat_interval_seconds: float = 15.0
    worker_shutdown_timeout_seconds: float = 30.0

    # n8n orchestration configuration (Phase 4)
    # backend_public_url is optional during local development; when omitted,
    # FastAPI generates http://127.0.0.1:8001/api/v1/integrations/n8n/callback.
    n8n_webhook_url: str = ""
    n8n_callback_secret: str = ""
    backend_public_url: Optional[str] = None
    n8n_request_timeout_seconds: float = 15.0
    deployment_mode: Literal["local", "hybrid_cloud", "docker_compose"] = "local"

    @model_validator(mode="before")
    @classmethod
    def ensure_consistent_project_env_settings(cls, values: Any) -> Any:
        """
        Ensure FastAPI, the embedded worker, and the standalone worker all resolve
        the exact same settings from PROJECT_ENV_FILE (`PROJECT_ROOT / ".env"`):
        - Never allow empty process environment variables (`""`) to silently override
          non-empty values configured in the project `.env` file.
        - Never allow stale container supervisor environment variables (`AI_PROVIDER=mock`
          or `AI_MODEL=gemini-4-carbon`) to silently switch runtime execution to mock mode
          when the project `.env` configures `gemini`.
        """
        if not isinstance(values, dict):
            return values

        # If APIFY_API_TOKEN is not in os.environ but APIFY_TOKEN is set in os.environ,
        # give the explicit process environment variable APIFY_TOKEN priority over .env file.
        if (
            "APIFY_API_TOKEN" not in os.environ
            and os.environ.get("APIFY_TOKEN", "").strip()
        ):
            values["apify_api_token"] = os.environ.get("APIFY_TOKEN")

        in_pytest = "PYTEST_CURRENT_TEST" in os.environ
        file_vals = read_project_dotenv_values()

        if not in_pytest:
            # 1. Prevent empty string process env overrides from masking valid project .env values
            for field_name, env_keys in (
                ("apify_api_token", ("APIFY_API_TOKEN", "APIFY_TOKEN")),
                ("gemini_api_key", ("GEMINI_API_KEY",)),
                ("ai_provider", ("AI_PROVIDER",)),
                ("ai_model", ("AI_MODEL",)),
                ("database_url", ("DATABASE_URL",)),
                ("discovery_execution_mode", ("DISCOVERY_EXECUTION_MODE",)),
                ("worker_enabled", ("WORKER_ENABLED",)),
            ):
                current_val = values.get(field_name)
                is_empty = current_val is None or str(current_val).strip() == ""
                if is_empty:
                    for ek in env_keys:
                        candidate = file_vals.get(ek, "").strip()
                        if candidate:
                            values[field_name] = candidate
                            break

            # 2. Prevent stale container supervisor AI_PROVIDER=mock from overriding project .env AI_PROVIDER=gemini
            file_ai_provider = file_vals.get("AI_PROVIDER", "").strip().strip("\"'").lower()
            if (
                str(values.get("ai_provider", "")).strip().lower() == "mock"
                and file_ai_provider == "gemini"
            ):
                values["ai_provider"] = "gemini"

        return values

    @field_validator("ai_provider", mode="before")
    @classmethod
    def sanitize_ai_provider(cls, value: Any) -> str:
        if value is None:
            return "gemini"
        cleaned = str(value).strip().strip("\"'").strip().lower()
        while cleaned.startswith("ai_provider="):
            cleaned = cleaned.split("=", 1)[1].strip().strip("\"'").strip().lower()
        if cleaned in {"mock", "gemini"}:
            return cleaned
        return "gemini"

    @field_validator("ai_model", mode="before")
    @classmethod
    def sanitize_ai_model(cls, value: Any) -> str:
        if value is None:
            return "gemini-3.1-flash-lite"
        cleaned = str(value).strip().strip("\"'").strip()
        while cleaned.upper().startswith("AI_MODEL="):
            cleaned = cleaned.split("=", 1)[1].strip().strip("\"'").strip()
        while cleaned.lower().startswith("models/"):
            cleaned = cleaned[7:].strip()
        if not cleaned or cleaned.lower() == "gemini-4-carbon":
            return "gemini-3.1-flash-lite"
        return cleaned

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def sanitize_gemini_api_key(cls, value: Any) -> str:
        if value is None:
            value = ""
        cleaned = str(value).strip().strip("\"'").strip()
        while (
            cleaned.upper().startswith("GEMINI_API_KEY=")
            or cleaned.upper().startswith("BEARER ")
        ):
            if cleaned.upper().startswith("BEARER "):
                cleaned = cleaned[7:].strip().strip("\"'").strip()
            else:
                cleaned = cleaned.split("=", 1)[1].strip().strip("\"'").strip()
        if _is_secret_placeholder(cleaned):
            if "PYTEST_CURRENT_TEST" not in os.environ:
                fallback = read_project_dotenv_values().get("GEMINI_API_KEY", "")
                fallback_clean = str(fallback).strip().strip("\"'").strip()
                if not _is_secret_placeholder(fallback_clean):
                    return fallback_clean
            return ""
        return cleaned

    @field_validator("apify_api_token", mode="before")
    @classmethod
    def sanitize_apify_api_token(cls, value: Any) -> str:
        if value is None or str(value).strip() == "":
            value = os.getenv("APIFY_TOKEN") or ""
            if not str(value).strip() and "PYTEST_CURRENT_TEST" not in os.environ:
                dotenv_map = read_project_dotenv_values()
                value = (
                    dotenv_map.get("APIFY_API_TOKEN")
                    or dotenv_map.get("APIFY_TOKEN")
                    or ""
                )
        cleaned = str(value).strip().strip("\"'").strip()
        while (
            cleaned.upper().startswith("APIFY_API_TOKEN=")
            or cleaned.upper().startswith("APIFY_TOKEN=")
            or cleaned.upper().startswith("BEARER ")
        ):
            if cleaned.upper().startswith("BEARER "):
                cleaned = cleaned[7:].strip().strip("\"'").strip()
            else:
                cleaned = cleaned.split("=", 1)[1].strip().strip("\"'").strip()
        if _is_secret_placeholder(cleaned):
            return ""
        return cleaned

    @field_validator("n8n_webhook_url", mode="before")
    @classmethod
    def sanitize_webhook_url(cls, value: Any) -> str:
        if value is None:
            return ""
        cleaned = str(value).strip()
        while cleaned.upper().startswith("N8N_WEBHOOK_URL="):
            cleaned = cleaned.split("=", 1)[1].strip()
        return cleaned

    @field_validator("backend_public_url", mode="before")
    @classmethod
    def sanitize_backend_public_url(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        while cleaned.upper().startswith("BACKEND_PUBLIC_URL="):
            cleaned = cleaned.split("=", 1)[1].strip()
        return cleaned or None

    @field_validator("deployment_mode", mode="before")
    @classmethod
    def normalize_deployment_mode(cls, value: Any) -> str:
        if value is None:
            return "local"
        cleaned = str(value).strip().lower()
        if cleaned in {"", "local", "dev", "development", "devlopment", "test", "testing"}:
            return "local"
        if cleaned in {"docker_compose", "docker-compose", "docker", "compose"}:
            return "docker_compose"
        if cleaned in {"hybrid", "hybrid_cloud", "cloud", "cloud_run", "prod", "production"}:
            return "hybrid_cloud"
        return cleaned

    # Allowed CORS origins and regex for web dashboards and preview hosts
    cors_origins: Union[list[str], str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://0.0.0.0:3000",
    ]
    cors_origin_regex: str = r"^https?://.*"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str]:
        if value is None:
            return ["http://localhost:3000", "http://127.0.0.1:3000", "http://0.0.0.0:3000"]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return ["http://localhost:3000", "http://127.0.0.1:3000", "http://0.0.0.0:3000"]
            if value.startswith("[") and value.endswith("]"):
                import json
                try:
                    return json.loads(value)
                except Exception:
                    pass
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return list(value)

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def get_effective_public_base_url(self) -> str:
        """
        Return configured backend public base URL without trailing slash.
        When BACKEND_PUBLIC_URL is unset or empty, falls back to local FastAPI
        address (http://127.0.0.1:8001 by default) for same-machine local testing.
        """
        configured = (self.backend_public_url or "").strip()
        if configured:
            return configured.rstrip("/")
        host = self.backend_host.strip() or "127.0.0.1"
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{self.backend_port}"

    def get_trusted_callback_url(self) -> str:
        """Build the trusted FastAPI callback URL exclusively from server configuration."""
        base_url = self.get_effective_public_base_url()
        return f"{base_url}/api/v1/integrations/n8n/callback"

    def validate_callback_url_for_hybrid(self, callback_url: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """
        Validate that the server-generated callback URL is a real, non-placeholder destination
        appropriate for the active deployment mode (local, docker_compose, or hybrid_cloud).
        """
        configured_public = (self.backend_public_url or "").strip()
        if configured_public and is_placeholder_url(configured_public):
            return (
                False,
                "BACKEND_PUBLIC_URL contains a placeholder value; leave it empty for local development "
                "(http://127.0.0.1:8001) or set a real callback URL.",
            )

        target = (callback_url or self.get_trusted_callback_url()).strip()
        if not target:
            return False, "Callback URL is empty."
        if is_placeholder_url(target):
            return False, "Callback URL cannot use a placeholder value."

        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "Callback URL must include a valid http/https scheme and hostname."
        if is_session_gated_preview_url(target):
            return (
                False,
                "Callback URL uses a session-gated AI Studio preview hostname (ais-dev/ais-pre), "
                "which blocks unauthenticated server-to-server n8n callbacks.",
            )

        if self.deployment_mode == "docker_compose":
            if not configured_public:
                return (
                    False,
                    "BACKEND_PUBLIC_URL is required when DEPLOYMENT_MODE='docker_compose'.",
                )
            if is_loopback_url(target):
                return (
                    False,
                    "Callback URL cannot use loopback (127.0.0.1/localhost) between separate Docker Compose containers.",
                )
            if parsed.scheme == "http" and not is_recognized_docker_service_url(
                target, _RECOGNIZED_DOCKER_CALLBACK_HOSTS
            ):
                return (
                    False,
                    "Callback URL over HTTP in docker_compose mode is permitted only for recognized internal "
                    "Docker service hostnames (e.g., leadforge-app, backend, gateway); external/public URLs must use HTTPS.",
                )
            return True, None

        if self.deployment_mode == "hybrid_cloud" or self.app_env.lower() == "production":
            if not configured_public:
                return (
                    False,
                    "BACKEND_PUBLIC_URL is required when DEPLOYMENT_MODE='hybrid_cloud' or APP_ENV='production'.",
                )
            if is_loopback_url(target):
                return (
                    False,
                    "Callback URL cannot use loopback (127.0.0.1/localhost) in hybrid/cloud deployment.",
                )
            if parsed.scheme != "https":
                return (
                    False,
                    "Callback URL must use HTTPS in hybrid/cloud deployment.",
                )
        return True, None

    def validate_webhook_url(self) -> tuple[bool, Optional[str]]:
        """Validate outbound n8n webhook URL according to deployment mode."""
        target = self.n8n_webhook_url.strip()
        if not target:
            return False, "N8N_WEBHOOK_URL is not configured."
        if is_placeholder_url(target):
            return (
                False,
                "N8N_WEBHOOK_URL contains a placeholder value; set a real local or tunnel webhook URL.",
            )
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "N8N_WEBHOOK_URL must include a valid http/https scheme and hostname."

        if self.deployment_mode == "docker_compose":
            if is_loopback_url(target):
                return (
                    False,
                    "N8N_WEBHOOK_URL cannot use 127.0.0.1 or localhost between separate Docker Compose containers.",
                )
            if parsed.scheme == "http" and not is_recognized_docker_service_url(
                target, _RECOGNIZED_DOCKER_WEBHOOK_HOSTS
            ):
                return (
                    False,
                    "N8N_WEBHOOK_URL over HTTP in docker_compose mode is permitted only for recognized internal "
                    "Docker service hostnames (e.g., n8n, leadforge-n8n); external/public URLs must use HTTPS.",
                )
            return True, None

        if self.deployment_mode == "hybrid_cloud" or self.app_env.lower() == "production":
            if is_loopback_url(target):
                return (
                    False,
                    "N8N_WEBHOOK_URL cannot use 127.0.0.1 or localhost in hybrid/cloud deployment; "
                    "use a public HTTPS tunnel to reach Windows n8n.",
                )
            if parsed.scheme != "https":
                return (
                    False,
                    "N8N_WEBHOOK_URL must use HTTPS in hybrid/cloud deployment.",
                )
        return True, None

    def get_sqlite_persistence_status(self) -> dict[str, object]:
        """Inspect SQLite storage configuration and report whether it is production-persistent."""
        in_cloud_run = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
        sqlite_path = (
            self.database_url.replace("sqlite:///", "")
            if self.database_url.startswith("sqlite:///")
            else self.database_url
        )
        durable_mount = self.sqlite_durable_mount_path.strip()
        is_mounted_durable = bool(
            durable_mount and os.path.abspath(sqlite_path).startswith(os.path.abspath(durable_mount))
        )
        is_production_persistent = is_mounted_durable or not (
            in_cloud_run
            or self.deployment_mode in {"hybrid_cloud", "docker_compose"}
            or self.app_env.lower() == "production"
        )
        return {
            "database_engine": "SQLite (SQLAlchemy ORM)",
            "sqlite_target": sqlite_path,
            "running_in_cloud_container": in_cloud_run,
            "durable_mount_configured": is_mounted_durable,
            "is_production_persistent": bool(is_mounted_durable),
            "storage_mode": (
                "durable_volume_mount"
                if is_mounted_durable
                else "local_ephemeral_filesystem"
            ),
            "persistence_warning": (
                None
                if is_mounted_durable
                else "Local container SQLite (./leadforge.db) is for local development only and is ephemeral on Cloud Run without a durable volume mount."
            ),
            "environment_appropriate": is_production_persistent,
        }

    def get_resolved_database_path(self) -> str:
        """Resolve the filesystem path for SQLite database URLs relative to PROJECT_ROOT."""
        if not self.database_url.startswith("sqlite:///"):
            return self.database_url
        raw_path = self.database_url[len("sqlite:///") :].strip()
        if not raw_path or raw_path == ":memory:":
            return raw_path or ":memory:"
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return str(candidate)

    def get_runtime_diagnostic(self) -> dict[str, object]:
        """
        Return safe, non-secret runtime configuration diagnostic fields:
        - Project root
        - Environment file path
        - AI provider
        - AI model
        - APIFY_API_TOKEN configured: true/false
        - GEMINI_API_KEY configured: true/false
        - Database path
        - Worker execution mode
        """
        apify_ok = bool(self.apify_api_token and self.apify_api_token.strip())
        gemini_ok = bool(
            self.gemini_api_key
            and self.gemini_api_key.strip()
            and not _is_secret_placeholder(self.gemini_api_key)
        )
        return {
            "project_root": str(PROJECT_ROOT),
            "env_file_path": str(PROJECT_ENV_FILE),
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "apify_api_token_configured": apify_ok,
            "gemini_api_key_configured": gemini_ok,
            "database_path": self.get_resolved_database_path(),
            "worker_execution_mode": self.discovery_execution_mode,
            "worker_enabled": bool(self.worker_enabled),
        }

    def format_runtime_diagnostic_log(self, component: str = "LeadForge") -> str:
        """Format a safe one-line configuration diagnostic log message with zero secret exposure."""
        diag = self.get_runtime_diagnostic()
        return (
            f"[{component} Config Diagnostic] "
            f"project_root={diag['project_root']} | "
            f"env_file_path={diag['env_file_path']} | "
            f"ai_provider={diag['ai_provider']} | "
            f"ai_model={diag['ai_model']} | "
            f"APIFY_API_TOKEN configured: {str(diag['apify_api_token_configured']).lower()} | "
            f"GEMINI_API_KEY configured: {str(diag['gemini_api_key_configured']).lower()} | "
            f"database_path={diag['database_path']} | "
            f"worker_execution_mode={diag['worker_execution_mode']} "
            f"(worker_enabled={str(diag['worker_enabled']).lower()})"
        )

    def get_public_summary(self) -> dict[str, object]:
        """Return safe, non-secret configuration metadata for status inspection."""
        sqlite_target = (
            self.database_url.replace("sqlite:///", "")
            if self.database_url.startswith("sqlite:///")
            else "configured"
        )
        webhook_valid, webhook_reason = self.validate_webhook_url()
        callback_valid, callback_reason = self.validate_callback_url_for_hybrid()
        sqlite_persistence = self.get_sqlite_persistence_status()
        runtime_diag = self.get_runtime_diagnostic()
        return {
            "app_name": self.app_name,
            "environment": self.app_env,
            "version": self.app_version,
            "deployment_mode": self.deployment_mode,
            "project_root": runtime_diag["project_root"],
            "env_file_path": runtime_diag["env_file_path"],
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_thinking_level": "HIGH",
            "apify_configured": runtime_diag["apify_api_token_configured"],
            "gemini_configured": runtime_diag["gemini_api_key_configured"],
            "worker_execution_mode": runtime_diag["worker_execution_mode"],
            "worker_enabled": runtime_diag["worker_enabled"],
            "database_engine": "SQLite (SQLAlchemy ORM)",
            "database_target": sqlite_target,
            "database_path": runtime_diag["database_path"],
            "sqlite_persistence": sqlite_persistence,
            "n8n_webhook_configured": bool(self.n8n_webhook_url.strip()),
            "n8n_webhook_valid": webhook_valid,
            "n8n_webhook_notice": webhook_reason,
            "callback_secret_configured": bool(self.n8n_callback_secret.strip()),
            "callback_url_valid": callback_valid,
            "callback_url_notice": callback_reason,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


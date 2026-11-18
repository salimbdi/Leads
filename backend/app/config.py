from functools import lru_cache
import os
from typing import Any, Literal, Optional
from urllib.parse import urlsplit
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    """Application settings loaded from environment variables or .env file."""

    app_name: str = "LeadForge"
    app_env: str = "development"
    app_version: str = "0.1.0"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8001

    # Database configuration (models implemented in Phase 2)
    database_url: str = "sqlite:///./leadforge.db"
    sqlite_durable_mount_path: str = ""

    # AI analysis provider configuration
    ai_provider: Literal["mock", "gemini"] = "mock"
    ai_model: str = "gemini-4-carbon"
    gemini_api_key: str = ""

    # n8n orchestration configuration (Phase 4)
    # backend_public_url is optional during local development; when omitted,
    # FastAPI generates http://127.0.0.1:8001/api/v1/integrations/n8n/callback.
    n8n_webhook_url: str = ""
    n8n_callback_secret: str = ""
    backend_public_url: Optional[str] = None
    n8n_request_timeout_seconds: float = 15.0
    deployment_mode: Literal["local", "hybrid_cloud", "docker_compose"] = "local"

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

    # Allowed CORS origins for local frontend development
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://0.0.0.0:3000",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
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
        return {
            "app_name": self.app_name,
            "environment": self.app_env,
            "version": self.app_version,
            "deployment_mode": self.deployment_mode,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_thinking_level": "HIGH",
            "database_engine": "SQLite (SQLAlchemy ORM)",
            "database_target": sqlite_target,
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


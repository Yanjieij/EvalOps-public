"""Process-wide configuration, loaded from environment variables.

We intentionally avoid a settings service — every knob is an env var with a
documented default. Override via a .env file, an explicit `EVALOPS_` env
variable, or the CLI (which takes precedence).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVALOPS_",
        env_file=(".env", ".env.local"),
        extra="ignore",
    )

    # --- Logging / observability ---
    log_level: str = "INFO"
    log_json: bool = False

    otel_exporter_endpoint: str = ""  # empty = disabled
    otel_service_name: str = "evalops-eval-engine"

    prometheus_port: int = 0  # 0 = disabled (CLI smoke runs don't need a server)

    # --- Reference SUT defaults (overridable per-run) ---
    reference_base_url: str = "http://localhost:8080"
    reference_user: str = ""
    reference_password: str = ""
    reference_timeout_s: float = 60.0

    # --- Judge models (Week 2+) ---
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    zhipu_api_key: str = ""

    # --- Runner ---
    default_concurrency: int = 4
    request_retry_max: int = 3
    request_retry_backoff_s: float = 0.5


_cached: Settings | None = None


def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached

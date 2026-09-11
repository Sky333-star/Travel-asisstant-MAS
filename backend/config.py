"""Typed application settings, loaded from the environment or a ``.env`` file.

Every knob the system exposes lives here so that behaviour is configurable
without code changes, and so that the "what does this deployment actually do"
question has one answer. Defaults are chosen so that ``uvicorn app.main:app``
works on a clean checkout with no ``.env`` at all -- the only thing a key
unlocks is LLM-quality reasoning.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/app/config.py -> backend/app -> backend -> repo.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All runtime configuration for the Wayfarer backend."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- LLM ----------------
    huggingface_api_key: str = Field(default="", description="HF Inference Providers token.")
    hf_chat_model: str = "Qwen/Qwen2.5-72B-Instruct"
    hf_fast_model: str = "Qwen/Qwen2.5-7B-Instruct"
    hf_stt_model: str = "openai/whisper-large-v3-turbo"
    hf_base_url: str = "https://router.huggingface.co/v1"
    hf_provider: str = "auto"
    hf_timeout_seconds: float = 90.0
    hf_max_retries: int = 3
    hf_temperature: float = 0.3
    hf_max_tokens: int = 1400

    # ---------------- Keyless data sources ----------------
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    open_meteo_air_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    frankfurter_url: str = "https://api.frankfurter.app"
    ourairports_url: str = (
        "https://davidmegginson.github.io/ourairports-data/airports.csv"
    )
    osm_user_agent: str = "WayfarerTravelAssistant/1.0 (contact: set-OSM_USER_AGENT)"
    nominatim_rate_limit_rps: float = 1.0
    overpass_rate_limit_rps: float = 0.5
    http_cache_ttl_seconds: int = 3600
    http_cache_dir: str = "data/cache"

    # ---------------- Backend ----------------
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = "console"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    checkpoint_db: str = "data/checkpoints/wayfarer.sqlite"
    max_plan_revisions: int = 3
    default_currency: str = "EUR"
    max_upload_bytes: int = 25 * 1024 * 1024

    # ---------------- MCP ----------------
    mcp_transport: str = "stdio"
    mcp_startup_timeout: float = 45.0
    mcp_tool_timeout: float = 60.0
    mcp_enabled_servers: str = "weather,geo,flights,hotels,currency"

    # ---------------- Observability ----------------
    otel_enabled: bool = True
    otel_service_name: str = "wayfarer-backend"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    metrics_enabled: bool = True
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3001"

    # ---------------- Derived helpers ----------------

    @field_validator("log_format")
    @classmethod
    def _check_log_format(cls, value: str) -> str:
        value = value.strip().lower()
        return value if value in ("console", "json") else "console"

    @property
    def llm_available(self) -> bool:
        """Whether LLM reasoning is enabled.

        When false the graph still runs end to end using the deterministic
        rule-based analyser and ranker -- degraded, but never broken.
        """
        return bool(self.huggingface_api_key.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_servers(self) -> list[str]:
        return [s.strip() for s in self.mcp_enabled_servers.split(",") if s.strip()]

    @property
    def checkpoint_path(self) -> Path:
        path = Path(self.checkpoint_db)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cache_path(self) -> Path:
        path = Path(self.http_cache_dir)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def langfuse_configured(self) -> bool:
        return bool(
            self.langfuse_enabled
            and self.langfuse_public_key.strip()
            and self.langfuse_secret_key.strip()
        )

    def mcp_server_env(self) -> dict[str, str]:
        """Environment handed to each spawned MCP server subprocess.

        The servers read their upstream URLs and rate limits from the
        environment, so the parent process is the single source of truth --
        change a URL in ``.env`` and every server picks it up on next launch.
        """
        return {
            **os.environ,
            "OPEN_METEO_FORECAST_URL": self.open_meteo_forecast_url,
            "OPEN_METEO_ARCHIVE_URL": self.open_meteo_archive_url,
            "OPEN_METEO_GEOCODE_URL": self.open_meteo_geocode_url,
            "OPEN_METEO_AIR_URL": self.open_meteo_air_url,
            "NOMINATIM_URL": self.nominatim_url,
            "OVERPASS_URL": self.overpass_url,
            "FRANKFURTER_URL": self.frankfurter_url,
            "OURAIRPORTS_URL": self.ourairports_url,
            "OSM_USER_AGENT": self.osm_user_agent,
            "NOMINATIM_RATE_LIMIT_RPS": str(self.nominatim_rate_limit_rps),
            "OVERPASS_RATE_LIMIT_RPS": str(self.overpass_rate_limit_rps),
            "HTTP_CACHE_TTL_SECONDS": str(self.http_cache_ttl_seconds),
            "HTTP_CACHE_DIR": str(self.cache_path),
            "MCP_TRANSPORT": "stdio",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


settings = get_settings()

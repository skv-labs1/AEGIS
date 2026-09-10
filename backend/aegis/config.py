"""Runtime configuration, read from the environment with sane local defaults."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    # Where Aegis keeps its own state. Never holds enterprise master data.
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'aegis.db'}"

    # Governance inputs.
    policy_file: Path = BACKEND_ROOT / "policy" / "risk_policy.yaml"
    upstreams_file: Path = BACKEND_ROOT / "mcp_upstreams.yaml"

    # Gateway MCP endpoint.
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8800

    # How long to wait on an upstream system before giving up.
    upstream_timeout_seconds: float = 20.0

    # Redaction and evidence retention are enforced when real data is connected.
    evidence_retention_days: int = 90


def get_settings() -> Settings:
    return Settings()

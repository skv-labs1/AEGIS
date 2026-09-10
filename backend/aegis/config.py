"""Runtime configuration, read from the environment with sane local defaults."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Configuration lives next to the code as editable files rather than as package
# data, because a risk policy an operator cannot find and read is not much of a
# control. That makes the install layout matter: the container installs the
# backend in editable mode so these paths still resolve.
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

    # How long a proposal waits for a human before it expires. A timeout is not
    # approval: the action simply does not run.
    approval_timeout_seconds: float = 300.0

    # Redaction and evidence retention are enforced when real data is connected.
    evidence_retention_days: int = 90

    # On a public demo, a live model run costs real quota and a stranger can
    # exhaust it. When set, live runs need this passcode; replay stays open to
    # everyone, which is what a visitor should get anyway.
    live_passcode: str = ""


    def require_files(self) -> None:
        """Fail with something an operator can act on, not a bare traceback."""
        for label, path in (
            ("risk policy", self.policy_file),
            ("upstream systems", self.upstreams_file),
        ):
            if not Path(path).is_file():
                raise FileNotFoundError(
                    f"The {label} file is missing: {path}. Aegis reads it at startup. "
                    f"Set AEGIS_{'POLICY' if label.startswith('risk') else 'UPSTREAMS'}_FILE, "
                    "or run from a checkout where it exists."
                )


def get_settings() -> Settings:
    return Settings()

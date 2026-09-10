"""Which enterprise systems Aegis connects to."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class UpstreamConfig:
    """One enterprise system, reachable as an MCP server.

    Replacing a demo system with a vendor one is a change to `url`. Credentials
    belong to that server's own environment and never reach Aegis.
    """

    name: str
    prefix: str
    url: str
    capability: str = ""
    description: str = ""
    enabled: bool = True

    def qualify(self, tool_name: str) -> str:
        """Namespace an upstream tool so two systems cannot collide."""
        return f"{self.prefix}_{tool_name}"


def load_upstreams(path: Path) -> list[UpstreamConfig]:
    with open(path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    upstreams: list[UpstreamConfig] = []
    seen_prefixes: set[str] = set()
    for entry in raw.get("upstreams") or []:
        config = UpstreamConfig(
            name=entry["name"],
            prefix=entry.get("prefix", entry["name"]),
            url=entry["url"],
            capability=entry.get("capability", ""),
            description=(entry.get("description") or "").strip(),
            enabled=bool(entry.get("enabled", True)),
        )
        if config.prefix in seen_prefixes:
            raise ValueError(
                f"Two upstreams share the prefix {config.prefix!r}. Prefixes must be unique "
                "or their tools would collide inside Aegis."
            )
        seen_prefixes.add(config.prefix)
        upstreams.append(config)
    return upstreams

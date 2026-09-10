"""Building the provider chain from configuration.

The chain is what makes a free-tier demo survive contact with rate limits: the
primary provider is tried, then each fallback in order, and the reason for each
switch is recorded. Moving to a paid provider later is an edit to
``llm_providers.yaml``, not to the engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .base import Completion, LLMProvider, Message, ProviderError, ToolSpec
from .gemini import GeminiProvider
from .openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)


def build_provider(name: str, spec: dict[str, Any]) -> LLMProvider:
    kind = spec.get("kind", "openai_compatible")
    if kind == "gemini":
        return GeminiProvider(
            model=spec["model"],
            api_key_env=spec.get("api_key_env", "GEMINI_API_KEY"),
            base_url=spec.get("base_url", "https://generativelanguage.googleapis.com/v1beta"),
            timeout=float(spec.get("timeout_seconds", 90.0)),
        )
    if kind == "openai_compatible":
        return OpenAICompatibleProvider(
            name=name,
            model=spec["model"],
            base_url=spec["base_url"],
            api_key_env=spec.get("api_key_env"),
            timeout=float(spec.get("timeout_seconds", 90.0)),
        )
    if kind == "anthropic":
        raise ProviderError(
            "The Anthropic adapter is not built. Aegis runs on free-tier providers by "
            "design; add an adapter if a paid key is wanted."
        )
    raise ProviderError(f"Unknown provider kind {kind!r} for provider {name!r}.")


@dataclass
class ProviderAttempt:
    provider: str
    model: str
    ok: bool
    error: str | None = None


@dataclass
class ProviderChain:
    """Tries each provider in turn and reports which one answered."""

    providers: list[LLMProvider]
    on_exhausted: str = "replay"
    attempts: list[ProviderAttempt] = field(default_factory=list)

    @property
    def primary(self) -> LLMProvider:
        return self.providers[0]

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.providers]

    @property
    def ready_names(self) -> list[str]:
        """Providers that have what they need to run, usually an API key."""
        return [p.name for p in self.providers if getattr(p, "ready", True)]

    @property
    def usable(self) -> bool:
        return bool(self.ready_names)

    def select(self, name: str) -> ProviderChain:
        """A chain that starts from a named provider, for a per-run override."""
        ordered = [p for p in self.providers if p.name == name]
        if not ordered:
            raise ProviderError(f"No provider named {name!r} is configured.")
        rest = [p for p in self.providers if p.name != name]
        return ProviderChain(providers=ordered + rest, on_exhausted=self.on_exhausted)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        errors: list[str] = []
        for provider in self.providers:
            try:
                completion = await provider.complete(
                    messages, tools, max_tokens=max_tokens, temperature=temperature
                )
            except ProviderError as exc:
                self.attempts.append(
                    ProviderAttempt(provider.name, provider.model, False, str(exc))
                )
                errors.append(f"{provider.name}: {exc}")
                if not exc.retryable:
                    logger.warning("Provider %s failed permanently: %s", provider.name, exc)
                else:
                    logger.info("Provider %s unavailable, trying the next: %s", provider.name, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - a provider may fail in any way
                self.attempts.append(
                    ProviderAttempt(provider.name, provider.model, False, repr(exc))
                )
                errors.append(f"{provider.name}: {exc!r}")
                continue

            self.attempts.append(ProviderAttempt(provider.name, provider.model, True))
            return completion

        raise ProviderError(
            "Every configured provider failed. " + " | ".join(errors),
            retryable=True,
        )


def load_chain(path: Path) -> ProviderChain:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    config = raw.get("llm") or {}
    specs = config.get("providers") or {}

    order: list[str] = []
    primary = config.get("primary")
    if primary:
        order.append(primary)
    order.extend(config.get("fallbacks") or [])

    providers: list[LLMProvider] = []
    for name in order:
        spec = specs.get(name)
        if spec is None:
            raise ProviderError(f"Provider {name!r} is referenced but not configured.")
        if spec.get("enabled") is False:
            continue
        providers.append(build_provider(name, spec))

    if not providers:
        raise ProviderError("No usable providers are configured in the LLM chain.")
    return ProviderChain(providers=providers, on_exhausted=config.get("on_exhausted", "replay"))

"""Risk classification and the approval decision.

Policy lives in YAML so a reviewer can read it without reading code, and so the
agent cannot influence it. The model may state an opinion about how risky an
action is; that opinion is recorded as evidence and this module decides.

The important rule is the default for a tool Aegis has never seen. Reads are
allowed and logged, so attaching a new system does not block an investigation.
Anything that does not positively declare itself read-only is treated as a
write and refused until an administrator classifies it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class RiskClass(str, enum.Enum):
    READ = "READ"
    WRITE_LOW = "WRITE_LOW"
    WRITE_MEDIUM = "WRITE_MEDIUM"
    WRITE_HIGH = "WRITE_HIGH"
    UNCLASSIFIED = "UNCLASSIFIED"

    @property
    def is_write(self) -> bool:
        return self in {RiskClass.WRITE_LOW, RiskClass.WRITE_MEDIUM, RiskClass.WRITE_HIGH}


class Decision(str, enum.Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    """Why a tool call was allowed, gated or refused."""

    decision: Decision
    risk: RiskClass
    tool: str
    reason: str
    source: str  # explicit | annotation_default | unclassified
    approver_roles: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()  # e.g. ("verification",)
    expected_effect: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "decision": self.decision.value,
            "risk_class": self.risk.value,
            "reason": self.reason,
            "classified_by": self.source,
            "approver_roles": list(self.approver_roles),
            "requires": list(self.requires),
            "expected_effect": self.expected_effect,
        }


@dataclass(frozen=True)
class Role:
    name: str
    label: str
    may_approve: frozenset[RiskClass]

    def can_approve(self, risk: RiskClass) -> bool:
        return risk in self.may_approve


@dataclass
class RiskPolicy:
    version: int
    roles: dict[str, Role]
    rules: dict[str, dict[str, Any]]
    unclassified_read: str
    unclassified_write: str
    unclassified_reason: str
    source_path: Path | None = None
    _warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> RiskPolicy:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw, source_path=path)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source_path: Path | None = None) -> RiskPolicy:
        defaults = (raw.get("defaults") or {}).get("unclassified") or {}
        roles: dict[str, Role] = {}
        for name, spec in (raw.get("roles") or {}).items():
            approvable = frozenset(RiskClass(value) for value in (spec.get("may_approve") or []))
            roles[name] = Role(name=name, label=spec.get("label", name), may_approve=approvable)

        rules = raw.get("tools") or {}
        policy = cls(
            version=int(raw.get("version", 1)),
            roles=roles,
            rules=rules,
            unclassified_read=defaults.get("read", "deny"),
            unclassified_write=defaults.get("write", "deny"),
            unclassified_reason=defaults.get(
                "reason", "Tool is not classified in the Aegis risk policy."
            ),
            source_path=source_path,
        )
        policy._validate()
        return policy

    def _validate(self) -> None:
        """Reject a policy that cannot mean what it says."""
        for tool, spec in self.rules.items():
            try:
                risk = RiskClass(spec["risk"])
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Policy rule for {tool!r} has an invalid risk class") from exc

            approval = spec.get("approval", "required")
            if approval not in {"never", "required", "denied"}:
                raise ValueError(
                    f"Policy rule for {tool!r} has approval={approval!r};"
                    " expected never, required or denied"
                )
            if approval == "never" and risk in {RiskClass.WRITE_MEDIUM, RiskClass.WRITE_HIGH}:
                raise ValueError(
                    f"Policy rule for {tool!r} lets a {risk.value} action run without approval."
                    " Medium and high risk writes always require a human."
                )
            for role in spec.get("roles", []) or []:
                if role not in self.roles:
                    raise ValueError(f"Policy rule for {tool!r} names unknown role {role!r}")
                if not self.roles[role].can_approve(risk):
                    raise ValueError(
                        f"Policy rule for {tool!r} lets role {role!r} approve a {risk.value}"
                        f" action, but that role may only approve"
                        f" {sorted(r.value for r in self.roles[role].may_approve)}"
                    )

    # -- classification -------------------------------------------------------

    @staticmethod
    def _looks_read_only(annotations: dict[str, Any] | None) -> bool:
        """Only a positive read-only declaration counts. Silence means write."""
        if not annotations:
            return False
        if annotations.get("destructive_hint") is True:
            return False
        return annotations.get("read_only_hint") is True

    def evaluate(self, tool: str, annotations: dict[str, Any] | None = None) -> PolicyDecision:
        """Classify a tool and decide whether it may run."""
        spec = self.rules.get(tool)
        if spec is None:
            return self._evaluate_unclassified(tool, annotations)

        risk = RiskClass(spec["risk"])
        approval = spec.get("approval", "required")
        reason = (spec.get("reason") or "").strip() or f"Classified {risk.value} by policy."
        roles = tuple(spec.get("roles") or [])
        requires_raw = spec.get("requires")
        requires = (
            tuple(requires_raw)
            if isinstance(requires_raw, list)
            else ((requires_raw,) if requires_raw else ())
        )

        if approval == "denied":
            decision = Decision.DENY
        elif approval == "required":
            decision = Decision.REQUIRE_APPROVAL
        else:
            decision = Decision.ALLOW

        return PolicyDecision(
            decision=decision,
            risk=risk,
            tool=tool,
            reason=reason,
            source="explicit",
            approver_roles=roles or self._roles_for(risk),
            requires=requires,
            expected_effect=spec.get("expected_effect"),
        )

    def _evaluate_unclassified(
        self, tool: str, annotations: dict[str, Any] | None
    ) -> PolicyDecision:
        read_only = self._looks_read_only(annotations)
        if read_only and self.unclassified_read == "allow_and_log":
            return PolicyDecision(
                decision=Decision.ALLOW,
                risk=RiskClass.UNCLASSIFIED,
                tool=tool,
                reason=(
                    f"{self.unclassified_reason} This tool declares itself read-only, so the "
                    "read is permitted and recorded."
                ),
                source="annotation_default",
            )
        declared = (
            "It does not declare itself read-only"
            if annotations
            else "It carries no annotations at all"
        )
        return PolicyDecision(
            decision=Decision.DENY,
            risk=RiskClass.UNCLASSIFIED,
            tool=tool,
            reason=(
                f"{self.unclassified_reason} {declared}, so it is treated as a write and "
                "refused. An administrator must classify it in the risk policy first."
            ),
            source="unclassified",
        )

    def _roles_for(self, risk: RiskClass) -> tuple[str, ...]:
        return tuple(sorted(n for n, r in self.roles.items() if r.can_approve(risk)))

    def role(self, name: str) -> Role | None:
        return self.roles.get(name)

    def classified_tools(self) -> list[str]:
        return sorted(self.rules)

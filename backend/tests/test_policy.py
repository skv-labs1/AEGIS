"""The policy engine decides what may run. Its defaults matter most."""

from __future__ import annotations

import pytest

from aegis.governance.policy import Decision, RiskClass, RiskPolicy

BASE = {
    "version": 1,
    "defaults": {"unclassified": {"read": "allow_and_log", "write": "deny", "reason": "Unknown."}},
    "roles": {
        "service_desk": {"label": "Service Desk", "may_approve": ["WRITE_LOW", "WRITE_MEDIUM"]},
        "it_admin": {"label": "Admin", "may_approve": ["WRITE_LOW", "WRITE_MEDIUM", "WRITE_HIGH"]},
        "auditor": {"label": "Auditor", "may_approve": []},
    },
    "tools": {},
}


def policy(tools: dict) -> RiskPolicy:
    return RiskPolicy.from_dict({**BASE, "tools": tools})


def test_read_tool_runs_without_approval():
    decision = policy({"itsm_get_incident": {"risk": "READ", "approval": "never"}}).evaluate(
        "itsm_get_incident"
    )
    assert decision.decision is Decision.ALLOW
    assert decision.risk is RiskClass.READ


def test_medium_risk_write_requires_approval():
    decision = policy(
        {
            "endpoint_clear_disk_space": {
                "risk": "WRITE_MEDIUM",
                "approval": "required",
                "roles": ["it_admin"],
            }
        }
    ).evaluate("endpoint_clear_disk_space")
    assert decision.decision is Decision.REQUIRE_APPROVAL
    assert decision.approver_roles == ("it_admin",)


def test_denied_tool_is_denied():
    decision = policy({"endpoint_reimage": {"risk": "WRITE_HIGH", "approval": "denied"}}).evaluate(
        "endpoint_reimage"
    )
    assert decision.decision is Decision.DENY


# -- the defaults for tools nobody has classified ----------------------------


def test_unknown_read_only_tool_is_allowed_and_logged():
    """Attaching a new system must not block an investigation from reading."""
    decision = policy({}).evaluate("newsystem_get_thing", {"read_only_hint": True})
    assert decision.decision is Decision.ALLOW
    assert decision.risk is RiskClass.UNCLASSIFIED
    assert decision.source == "annotation_default"


def test_unknown_write_tool_is_denied():
    decision = policy({}).evaluate("newsystem_delete_thing", {"destructive_hint": True})
    assert decision.decision is Decision.DENY
    assert decision.risk is RiskClass.UNCLASSIFIED


def test_unannotated_tool_is_treated_as_a_write_and_denied():
    """Silence is not a read-only declaration. This is the important default."""
    decision = policy({}).evaluate("mystery_tool", None)
    assert decision.decision is Decision.DENY
    assert "no annotations at all" in decision.reason


def test_a_tool_claiming_both_read_only_and_destructive_is_treated_as_a_write():
    """A contradictory declaration must resolve the safe way."""
    decision = policy({}).evaluate(
        "confused_tool", {"read_only_hint": True, "destructive_hint": True}
    )
    assert decision.decision is Decision.DENY


# -- policy files that cannot mean what they say -----------------------------


def test_policy_rejects_a_write_that_skips_approval():
    with pytest.raises(ValueError, match="without approval"):
        policy({"endpoint_wipe": {"risk": "WRITE_HIGH", "approval": "never"}})


def test_policy_rejects_an_unknown_role():
    with pytest.raises(ValueError, match="unknown role"):
        policy({"x": {"risk": "WRITE_LOW", "approval": "required", "roles": ["nobody"]}})


def test_policy_rejects_a_role_approving_above_its_level():
    """An auditor approving nothing is a control, so the file must not contradict it."""
    with pytest.raises(ValueError, match="may only approve"):
        policy({"x": {"risk": "WRITE_MEDIUM", "approval": "required", "roles": ["auditor"]}})


def test_policy_rejects_an_invalid_risk_class():
    with pytest.raises(ValueError, match="invalid risk class"):
        policy({"x": {"risk": "SORT_OF_RISKY", "approval": "required"}})


def test_shipped_policy_is_valid_and_denies_reimaging():
    """The policy the project actually ships must load and hold its boundaries."""
    from aegis.config import get_settings

    loaded = RiskPolicy.load(get_settings().policy_file)
    assert loaded.version == 1
    reimage = loaded.evaluate("endpoint_reimage_device")
    assert reimage.decision is Decision.DENY
    for tool in loaded.classified_tools():
        decision = loaded.evaluate(tool)
        if decision.risk in (RiskClass.WRITE_MEDIUM, RiskClass.WRITE_HIGH):
            assert decision.decision is not Decision.ALLOW, (
                f"{tool} is a {decision.risk.value} action that runs without a human"
            )

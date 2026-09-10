"""The headline demo scenario, walked end to end over MCP.

INC-1042: a finance executive reports a slow laptop and repeated Outlook
crashes. This test asserts that the evidence needed to reach the real root
cause is discoverable through the tools, that remediation genuinely changes
device state, and that verification can therefore be measured rather than
assumed. If this test fails, the demo does not work.
"""

from __future__ import annotations

from mcp import Client

from aegis_demo.endpoint.server import server as endpoint_server
from aegis_demo.itam.server import server as itam_server
from aegis_demo.itsm.server import server as itsm_server


async def call(server, name: str, args: dict | None = None) -> dict:
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(name, args or {})
        assert not result.is_error, f"{name} errored: {result.content}"
        return result.structured_content


async def test_investigation_evidence_chain_is_discoverable(demo_db):
    # 1. The incident identifies the caller.
    incident = (await call(itsm_server, "get_incident", {"number": "INC-1042"}))["incident"]
    assert incident["caller"]["user_id"] == "USR-1001"
    assert incident["caller"]["vip"] is True, "VIP status should be visible for prioritisation"

    # 2. Asset management resolves the caller to their primary machine.
    assets = await call(itam_server, "get_assets_for_user", {"user_id": "USR-1001"})
    device_id = assets["primary_compute"]
    assert device_id == "DEV-4411"

    # 3. Endpoint health shows a critical device and names what is wrong.
    health = await call(endpoint_server, "get_device_health", {"device_id": device_id})
    assert health["band"] == "critical"
    causes = {p["component"] for p in health["penalties"]}
    assert {"disk_utilisation", "application_stability", "cpu_utilisation"} <= causes
    assert health["telemetry"]["disk_used_pct"] >= 95

    # 4. The disk pressure is attributable, and most of it is Outlook's own data.
    reclaimable = health["reclaimable_disk"]
    assert reclaimable["total_gb"] > 100
    largest = max(reclaimable["categories"], key=lambda c: c["gb"])
    assert largest["category"] == "orphaned_outlook_data_files"

    # 5. Patching is failing *because of* the disk, not independently.
    patches = await call(endpoint_server, "get_patch_status", {"device_id": device_id})
    assert patches["missing_critical_count"] == 2
    assert any("disk space" in reason for reason in patches["common_failure_reasons"])

    # 6. The installed Office build carries a documented defect matching the symptom.
    software = await call(endpoint_server, "get_installed_software", {"device_id": device_id})
    office = next(s for s in software["software"] if "Microsoft 365" in s["name"])
    assert office["is_outdated"] is True
    issue = next(i for i in office["known_issues"] if "OUTLOOK" in i["symptom"].upper())
    assert "disk space" in issue["title"].lower()

    # 7. History shows this has happened before and was never actually fixed.
    history = await call(
        itsm_server,
        "search_incidents",
        {"caller_id": "USR-1001", "text": "outlook", "exclude_number": "INC-1042"},
    )
    assert history["count"] >= 4
    assert history["summary"]["resolved_as_workaround"] >= 4

    # 8. Degradation was gradual, not a sudden failure.
    trend = (await call(endpoint_server, "get_health_history", {"device_id": device_id}))["trend"]
    assert trend["direction"] == "declining"
    assert trend["disk_used_pct_change"] > 10

    # 9. The hardware is out of warranty and overdue for refresh, which shapes the advice.
    warranty = await call(itam_server, "get_warranty", {"identifier": device_id})
    assert warranty["warranty_active"] is False
    assert warranty["refresh_position"] == "overdue_for_refresh"


async def test_remediation_changes_state_and_verification_measures_it(demo_db):
    before = await call(endpoint_server, "get_device_health", {"device_id": "DEV-4411"})
    assert before["band"] == "critical"

    cleanup = await call(endpoint_server, "clear_disk_space", {"device_id": "DEV-4411"})
    assert cleanup["succeeded"] is True
    assert cleanup["result"]["reclaimed_gb"] > 100

    restart = await call(
        endpoint_server,
        "restart_application",
        {"device_id": "DEV-4411", "process_name": "OUTLOOK"},
    )
    assert restart["result"]["crash_count_cleared"] == 4

    after = await call(endpoint_server, "get_device_health", {"device_id": "DEV-4411"})

    # Verification is a real measurement of changed state, not an assumption.
    assert after["health_score"] > before["health_score"] + 30
    assert after["telemetry"]["disk_used_pct"] < 75
    assert after["telemetry"]["app_crashes_7d"].get("OUTLOOK.EXE") is None
    assert after["band"] in ("fair", "healthy")


async def test_remediation_does_not_fully_resolve_without_the_software_update(demo_db):
    """Honest outcome: cleanup fixes the symptom but the outdated Office build remains.

    The agent should recommend the update as a separate, higher-risk action rather
    than claim the incident is fully resolved.
    """
    await call(endpoint_server, "clear_disk_space", {"device_id": "DEV-4411"})
    await call(
        endpoint_server, "restart_application", {"device_id": "DEV-4411", "process_name": "OUTLOOK"}
    )
    after = await call(endpoint_server, "get_device_health", {"device_id": "DEV-4411"})

    assert after["band"] != "healthy", "a residual risk must remain visible after cleanup"
    remaining = {p["component"] for p in after["penalties"]}
    assert "software_currency" in remaining
    assert "patch_compliance" in remaining, "patches still need a retry and a reboot"


async def test_other_devices_are_unaffected_by_remediation(demo_db):
    """Actions must be scoped to their target device."""
    other_before = await call(endpoint_server, "get_device_health", {"device_id": "DEV-4412"})
    await call(endpoint_server, "clear_disk_space", {"device_id": "DEV-4411"})
    other_after = await call(endpoint_server, "get_device_health", {"device_id": "DEV-4412"})
    assert other_before["health_score"] == other_after["health_score"]

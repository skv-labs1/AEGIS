"""The generated background fleet.

Two things matter about it. It must be identical every time, or a demo looks
different on the second run. And it must leave the hand-authored INC-1042 story
exactly as it was, because that story is what the demo actually shows.
"""

from __future__ import annotations

import json

from aegis_demo.common import db as dbmod
from aegis_demo.common import fleet
from aegis_demo.endpoint import server as endpoint
from aegis_demo.itam import server as itam
from aegis_demo.itsm import server as itsm


def test_generation_is_deterministic():
    first = fleet.generate()
    second = fleet.generate()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_generated_records_cannot_collide_with_the_story():
    generated = fleet.generate()
    spec = fleet.load_spec()
    ids = spec["id_ranges"]

    assert all(int(u["user_id"].split("-")[1]) >= ids["user_start"] for u in generated["users"])
    assert all(
        int(d["device_id"].split("-")[1]) >= ids["device_start"] for d in generated["devices"]
    )

    # Incident numbering is the subtle one. ``create_incident`` allocates the
    # next number above the highest in the table, and an eval scenario depends
    # on that landing on INC-1045. Generated incidents therefore sit *below* the
    # story range rather than above it.
    numbers = [int(i["number"].split("-")[1]) for i in generated["incidents"]]
    assert max(numbers) < 871, "generated incidents must stay below the story range"


def test_the_story_survives_the_fleet(demo_db):
    conn = dbmod.connect(demo_db)
    try:
        device = json.loads(
            conn.execute("SELECT record FROM devices WHERE device_id='DEV-4411'").fetchone()[
                "record"
            ]
        )
        assert device["compliance_state"] == "non_compliant"

        health = endpoint.get_device_health("DEV-4411")
        assert health["health_score"] < 50
        assert health["telemetry"]["disk_used_pct"] == 97.0

        incident = itsm.get_incident("INC-1042")
        assert incident["incident"]["caller"]["display_name"] == "Priya Raghavan"
        assert incident["incident"]["state"] == "in_progress"

        highest = conn.execute("SELECT MAX(number) n FROM incidents").fetchone()["n"]
        assert highest == "INC-1044", "create_incident must still allocate INC-1045"
    finally:
        conn.close()


def test_fleet_summary_agrees_with_the_per_device_reads(demo_db):
    summary = endpoint.get_fleet_summary()

    assert summary["device_count"] >= 60
    assert sum(summary["health"]["bands"].values()) == summary["scored_device_count"]
    assert summary["patching"]["devices_missing_critical"] >= 1
    assert "0x80070070 - insufficient disk space" in summary["patching"]["failure_reasons"]

    worst = summary["devices_needing_attention"][0]
    assert worst["device_id"] == "DEV-4411"
    # The fleet-wide score is computed from bulk reads; it must match the score
    # the single-device tool reports, or the dashboard and the drill-down would
    # tell an operator two different things.
    assert worst["health_score"] == endpoint.get_device_health("DEV-4411")["health_score"]


def test_incident_summary_counts_what_the_search_tool_counts(demo_db):
    summary = itsm.get_incident_summary()

    assert summary["total"] == sum(summary["by_state"].values())
    assert summary["resolution_quality"]["resolved_as_workaround"] > 0

    priya = next(c for c in summary["repeat_callers"] if c["caller_id"] == "USR-1001")
    assert priya["vip"] is True
    searched = itsm.search_incidents(caller_id="USR-1001", limit=100)
    assert priya["incidents"] == searched["count"]
    assert priya["workarounds"] == searched["summary"]["resolved_as_workaround"]


def test_asset_summary_covers_lifecycle_warranty_and_spares(demo_db):
    summary = itam.get_asset_summary()

    assert summary["total"] == sum(summary["by_lifecycle_state"].values())
    assert summary["warranty"]["in_warranty"] + summary["warranty"]["out_of_warranty"] == (
        summary["total"]
    )
    assert summary["by_lifecycle_state"]["in_stock"] == sum(
        summary["spares_by_subcategory"].values()
    )
    assert summary["by_lifecycle_state"]["in_stock"] == itam.get_spare_inventory()["count"]
    assert any(c["device_id"] == "DEV-4411" for c in summary["refresh"]["candidates"])


def test_repeat_totals_are_not_taken_from_the_truncated_list(demo_db):
    """The headline figure must count every repeat, not the top few shown."""
    summary = itsm.get_incident_summary(top_repeat=3)

    assert len(summary["repeat_devices"]) <= 3
    assert summary["repeats"]["devices_affected"] > len(summary["repeat_devices"])
    assert summary["repeats"]["incidents_on_repeat_devices"] > sum(
        d["incidents"] for d in summary["repeat_devices"]
    )


def test_an_incident_resolved_during_the_demo_still_reports_its_age(demo_db):
    """Seed records carry a precomputed age; one closed live does not."""
    itsm.resolve_incident(
        "INC-1042",
        resolution_code="Solved (Workaround)",
        resolution_notes="Closed during the demo.",
    )
    summary = itsm.get_incident_summary()
    example = next(
        e for e in summary["resolution_quality"]["examples"] if e["number"] == "INC-1042"
    )
    assert example["resolved_days_ago"] == 0

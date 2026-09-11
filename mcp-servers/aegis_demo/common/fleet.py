"""Generate the background fleet.

The eight devices, ten users and twelve incidents in the other seed files are
the demo's story records: DEV-4411, Priya Raghavan, INC-1042 and the four
earlier Outlook incidents that the whole investigation turns on. Those are
hand-authored and this module never touches them.

What this adds is everything around them. A dashboard built on eight devices
reads as a toy no matter how good the underlying work is, so the fleet is
padded out to something that looks like a real estate. Generation is
deterministic, seeded from a fixed string, so the same spec always produces the
same fleet and an eval run is still reproducible.

Two constraints worth knowing before changing anything here:

- Generated incidents are numbered below the story range on purpose. ITSM picks
  the next incident number from the current maximum, so numbering underneath
  leaves that behaviour untouched.
- Generated identifiers start well past the hand-authored ones, so nothing can
  collide.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
RANDOM_SEED = "aegis-fleet-v1"


def load_spec(path: Path | None = None) -> dict[str, Any]:
    with open(path or SEED_DIR / "fleet.json", encoding="utf-8") as fh:
        return json.load(fh)


def _weighted(rng: random.Random, items: list[dict[str, Any]]) -> dict[str, Any]:
    return rng.choices(items, weights=[i.get("weight", 1) for i in items], k=1)[0]


def _between(rng: random.Random, bounds: list[int]) -> int:
    return rng.randint(int(bounds[0]), int(bounds[1]))


def _email(first: str, last: str, taken: set[str]) -> str:
    base = f"{first.lower()}.{last.lower()}"
    candidate = f"{base}@northwind.example"
    suffix = 2
    while candidate in taken:
        candidate = f"{base}{suffix}@northwind.example"
        suffix += 1
    taken.add(candidate)
    return candidate


def generate(spec: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Build the background fleet. Deterministic for a given spec."""
    spec = spec or load_spec()
    rng = random.Random(RANDOM_SEED)
    ids = spec["id_ranges"]
    counts = spec["counts"]

    users: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    software: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []

    emails_taken: set[str] = set()
    device_count = int(counts["devices"])

    # -- people and their machines -------------------------------------------

    for index in range(device_count):
        user_id = f"USR-{ids['user_start'] + index}"
        device_id = f"DEV-{ids['device_start'] + index}"
        asset_tag = f"AST-{ids['asset_start'] + index}"

        first = rng.choice(spec["first_names"])
        last = rng.choice(spec["last_names"])
        department = _weighted(rng, spec["departments"])
        location = _weighted(rng, spec["locations"])
        hardware = _weighted(rng, spec["hardware"])
        profile = _weighted(rng, spec["health_profiles"])
        os_spec = spec["operating_systems"][hardware["os"]]

        users.append(
            {
                "user_id": user_id,
                "display_name": f"{first} {last}",
                "email": _email(first, last, emails_taken),
                "job_title": rng.choice(spec["job_titles"]),
                "department": department["name"],
                "location": location["name"],
                "manager_id": None,
                "vip": False,
                "cost_centre": department["cost_centre"],
                "employment_type": "full_time",
            }
        )

        disk_used = _between(rng, profile["disk_used_pct"])
        cpu_avg = _between(rng, profile["cpu_avg_pct"])
        memory_used = _between(rng, profile["memory_used_pct"])
        reboot_days = _between(rng, profile["days_since_reboot"])

        crashes: dict[str, int] = {}
        if rng.random() < float(profile["crash_chance"]):
            crashes[rng.choice(spec["crashing_processes"])] = rng.randint(1, 4)

        missing_critical = int(profile["missing_critical"])
        missing_important = int(profile["missing_important"])
        pending_reboot = missing_critical > 0 and rng.random() < 0.6

        compliance_reasons = []
        if missing_critical:
            compliance_reasons.append("missing_critical_patches")
        if disk_used >= 90:
            compliance_reasons.append("disk_space_critical")
        if pending_reboot:
            compliance_reasons.append("reboot_pending")

        # Reclaimable space scales with how full the disk is, so a cleanup
        # recommendation on a congested machine has something behind it.
        reclaimable = []
        if disk_used >= 70:
            reclaimable.append(
                {
                    "category": "windows_update_cache",
                    "gb": round(rng.uniform(4, 16), 1),
                    "detail": "SoftwareDistribution download cache",
                }
            )
        reclaimable.append(
            {
                "category": "temp_files",
                "gb": round(rng.uniform(2, 11), 1),
                "detail": "User and system temporary files",
            }
        )

        devices.append(
            {
                "device_id": device_id,
                "hostname": f"NW-{location['code']}-L{ids['device_start'] + index}",
                "serial_number": f"{rng.choice('PFGHKN')}{rng.randint(1, 9)}"
                f"{''.join(rng.choices('ABCDEFGHJKLMNPQRSTUVWXYZ', k=2))}"
                f"{rng.randint(10, 99)}{rng.choice('BCDFGHJKLMNPQRSTVWXYZ')}",
                "primary_user_id": user_id,
                "manufacturer": hardware["manufacturer"],
                "model": hardware["model"],
                "form_factor": "laptop",
                "os_name": os_spec["name"],
                "os_version": os_spec["version"],
                "os_build": os_spec["build"],
                "cpu_model": "Intel Core i5-1245U"
                if hardware["manufacturer"] != "Apple"
                else "Apple M3",
                "cpu_cores": 10,
                "total_memory_gb": hardware["memory_gb"],
                "total_disk_gb": hardware["disk_gb"],
                "mdm_enrolled": True,
                "mdm_platform": "demo-endpoint-manager",
                "compliance_state": "non_compliant" if compliance_reasons else "compliant",
                "compliance_reasons": compliance_reasons,
                "encryption_enabled": rng.random() > 0.04,
                "last_seen_hours_ago": rng.choice([1, 1, 2, 2, 3, 4, 6, 9, 26, 51]),
                "telemetry": {
                    "disk_used_pct": float(disk_used),
                    "cpu_avg_pct": float(cpu_avg),
                    "cpu_baseline_pct": float(max(18, cpu_avg - rng.randint(4, 26))),
                    "memory_used_pct": float(memory_used),
                    "memory_baseline_pct": float(max(35, memory_used - rng.randint(3, 18))),
                    "days_since_reboot": reboot_days,
                    "pending_reboot": pending_reboot,
                    "app_crashes_7d": crashes,
                },
                "disk_reclaimable": reclaimable,
            }
        )

        purchased_days_ago = rng.randint(90, 1250)
        warranty_days_left = 1095 - purchased_days_ago
        assets.append(
            {
                "asset_tag": asset_tag,
                "device_id": device_id,
                "assigned_user_id": user_id,
                "category": "End User Compute",
                "subcategory": "Laptop",
                "manufacturer": hardware["manufacturer"],
                "model": hardware["model"],
                "serial_number": devices[-1]["serial_number"],
                "purchase_days_ago": purchased_days_ago,
                "purchase_cost": hardware["cost"],
                "currency": "CAD",
                "warranty_expires_in_days": warranty_days_left,
                "warranty_provider": f"{hardware['manufacturer']} Support",
                "lifecycle_state": "in_use",
                "refresh_due_in_days": warranty_days_left,
                "cost_centre": department["cost_centre"],
                "location": location["name"],
                "notes": None,
            }
        )

        # -- installed software ----------------------------------------------

        available = [
            item for item in spec["software_catalog"] if hardware["os"] in item["platforms"]
        ]
        installed = []
        for item in rng.sample(available, k=min(len(available), rng.randint(3, 6))):
            outdated = rng.random() < float(profile["outdated_chance"])
            installed.append(
                {
                    "name": item["name"],
                    "publisher": item["publisher"],
                    "installed_version": item["stale"] if outdated else item["current"],
                    "latest_available_version": item["current"],
                    "update_channel": "Managed",
                    "release_age_days": rng.randint(300, 900) if outdated else rng.randint(5, 60),
                    "is_outdated": outdated,
                    "is_business_critical": bool(item["critical"]),
                    "known_issues": [],
                }
            )
        software.append({"device_id": device_id, "installed": installed})

        # -- patch posture ----------------------------------------------------

        missing = []
        for entry in rng.sample(
            spec["patch_catalog"]["critical"], k=min(missing_critical, 3)
        ):
            reason = rng.choice(spec["patch_failure_reasons"])
            missing.append(
                {
                    **entry,
                    "severity": "critical",
                    "classification": "Security Update",
                    "released_days_ago": rng.randint(14, 70),
                    "reboot_required": True,
                    "install_state": "download_failed" if reason else "pending_install",
                    "failure_reason": reason,
                }
            )
        for entry in rng.sample(
            spec["patch_catalog"]["important"], k=min(missing_important, 3)
        ):
            missing.append(
                {
                    **entry,
                    "severity": "important",
                    "classification": "Security Update",
                    "released_days_ago": rng.randint(20, 90),
                    "reboot_required": False,
                    "install_state": "pending_install",
                    "failure_reason": None,
                }
            )
        patches.append(
            {
                "device_id": device_id,
                "last_scan_hours_ago": rng.randint(1, 40),
                "missing": missing,
                "installed_recent": [
                    {
                        "patch_id": "KB5034765",
                        "title": "2024-02 Cumulative Update",
                        "installed_days_ago": rng.randint(30, 90),
                        "severity": "critical",
                    }
                ],
            }
        )

    # -- spare stock ----------------------------------------------------------
    # Unassigned assets sitting in the depot. They have no device record: a
    # machine in a cupboard is not enrolled and reports no telemetry.
    spare_tag = int(ids["asset_start"]) + int(counts["devices"]) + 1
    for _ in range(int(counts.get("spares", 0))):
        hardware = rng.choice(spec["hardware"])
        purchased_days_ago = rng.randint(20, 240)
        assets.append(
            {
                "asset_tag": f"AST-{spare_tag}",
                "device_id": None,
                "assigned_user_id": None,
                "category": "End User Compute",
                "subcategory": "Laptop",
                "manufacturer": hardware["manufacturer"],
                "model": hardware["model"],
                "serial_number": f"SP{rng.randint(100000, 999999)}",
                "purchase_days_ago": purchased_days_ago,
                "purchase_cost": hardware["cost"],
                "currency": "CAD",
                "warranty_expires_in_days": 1095 - purchased_days_ago,
                "warranty_provider": f"{hardware['manufacturer']} Support",
                "lifecycle_state": "in_stock",
                "refresh_due_in_days": 1095 - purchased_days_ago,
                "cost_centre": "CC-1000",
                "location": rng.choice(spec["locations"])["name"],
                "notes": "Held in depot stock, ready for deployment.",
            }
        )
        spare_tag += 1

    # -- incidents ------------------------------------------------------------

    numbers = iter(
        sorted(
            rng.sample(
                range(int(ids["incident_start"]), int(ids["incident_end"])),
                k=int(counts["incidents"]),
            )
        )
    )
    templates = spec["incident_templates"]

    # A few machines carry several incidents that were each closed with a
    # workaround. That repeat-without-root-cause pattern is what the dashboard
    # surfaces, and it mirrors what INC-1042 turns out to be.
    repeat_spec = spec["repeat_offenders"]
    offenders = rng.sample(devices, k=int(repeat_spec["devices"]))
    offender_quota = {
        d["device_id"]: rng.randint(*repeat_spec["incidents_each"]) for d in offenders
    }

    def make_incident(number: int, device: dict[str, Any], force_workaround: bool) -> dict[str, Any]:
        template = rng.choice(templates)
        resolved = force_workaround or rng.random() < 0.74
        # A live queue is young: anything still open was raised in the last
        # couple of weeks. Only closed incidents reach back across the history.
        opened_days_ago = rng.randint(1, 200) if resolved else rng.randint(0, 12)

        if resolved:
            code = (
                "Solved (Workaround)"
                if force_workaround or rng.random() < 0.45
                else "Solved (Permanently)"
            )
            state = "resolved"
            resolved_days_ago = max(0, opened_days_ago - rng.randint(0, 3))
            notes = (
                "Symptom cleared for the user. No underlying cause identified."
                if code.endswith("(Workaround)")
                else "Cause addressed and confirmed with the user."
            )
        else:
            code = None
            state = rng.choice(["new", "in_progress", "in_progress"])
            resolved_days_ago = None
            notes = None

        return {
            "number": f"INC-{number:04d}",
            "short_description": template["short"],
            "description": (
                f"{template['short']}. Reported through the service desk. "
                "Background record in the demo fleet."
            ),
            "caller_id": device["primary_user_id"],
            "device_id": device["device_id"] if template["device"] else None,
            "state": state,
            "priority": rng.choice([2, 3, 3, 3, 4]),
            "impact": 3,
            "urgency": 3,
            "category": template["category"],
            "subcategory": template["subcategory"],
            "assignment_group": rng.choice(spec["assignment_groups"]),
            "assigned_to_id": None,
            "opened_days_ago": opened_days_ago,
            "opened_hours_ago": rng.randint(0, 20),
            "resolved_days_ago": resolved_days_ago,
            "channel": rng.choice(["self_service_portal", "phone", "email"]),
            "work_notes": [],
            "resolution_code": code,
            "resolution_notes": notes,
            "reopen_count": 1 if force_workaround and rng.random() < 0.4 else 0,
        }

    for device_id, quota in offender_quota.items():
        device = next(d for d in devices if d["device_id"] == device_id)
        for _ in range(quota):
            try:
                incidents.append(make_incident(next(numbers), device, force_workaround=True))
            except StopIteration:
                break

    for number in numbers:
        incidents.append(make_incident(number, rng.choice(devices), force_workaround=False))

    return {
        "users": users,
        "devices": devices,
        "assets": assets,
        "software": software,
        "patches": patches,
        "incidents": incidents,
    }

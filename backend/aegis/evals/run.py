"""Run the eval scenarios.

    python -m aegis.evals.run                 # every scenario the setup can run
    python -m aegis.evals.run --provider groq

Scenarios that cannot run (no model key and no stored trace) are reported as
skipped rather than silently dropped, because a suite that quietly shrinks is
worse than one that fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from ..config import get_settings
from ..db.session import init_engine, session_scope
from ..engine.providers.registry import ProviderChain, load_chain
from ..gateway.server import AegisGateway
from ..governance.approvals import ApprovalOutcome, ApprovalState, AutoApproveForTesting
from ..replay.provider import ReplayProvider
from ..replay.trace import available_traces
from .runner import EvalReport, load_scenarios, run_scenario, store


class RejectEverything:
    """Approver for the rejection scenario. Says no to everything, with a reason."""

    async def request(self, request):
        return ApprovalOutcome(
            state=ApprovalState.REJECTED,
            approver="Rebecca Lindqvist",
            approver_role="it_admin",
            note="Quarter-end close is running. No changes to this device today.",
        )


# Some scenarios need the agent itself to behave differently, which a fixed
# trace cannot do. Under replay they are skipped rather than mis-scored.
NEEDS_LIVE_MODEL = {
    "clear only the smallest disk category",
    # A trace cannot react to a rejection: it replays the turns it recorded and
    # would blindly try to execute anyway. Testing that an agent stands down
    # gracefully needs a model that can adapt.
    "reject every approval",
}


def _reseed() -> None:
    """Reset both sides before every scenario.

    The synthetic enterprise and Aegis's own state must both go back to a known
    point. Resetting only the enterprise leaves the previous scenario's
    investigation open, and the next `start_investigation` is then refused, so
    scores would silently depend on the order scenarios happened to run in.
    """
    from aegis_demo.common import db as demo_db
    from aegis_demo.common import seed
    from sqlalchemy import text

    conn = demo_db.connect()
    try:
        seed.build(conn)
    finally:
        conn.close()

    with session_scope() as db:
        for table in (
            "verifications",
            "proposals",
            "investigations",
            "evidence",
            "audit_events",
            "gateway_sessions",
        ):
            db.execute(text(f"DELETE FROM {table}"))


def _seed_incident(gateway):
    """Create the ticket a scenario needs, through the gateway like anything else."""

    async def create(scenario) -> None:
        await gateway.invoke("itsm_create_incident", scenario.seed_incident)

    return create


async def main_async(provider: str | None, only: list[str] | None, save: bool) -> int:
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    init_engine(settings)

    live = None
    try:
        live = load_chain(settings.policy_file.parent.parent / "llm_providers.yaml")
        if provider:
            live = live.select(provider)
        if not live.usable:
            live = None
    except Exception:  # noqa: BLE001 - absence is a normal state here
        live = None

    traces = available_traces()
    scenarios = [s for s in load_scenarios() if not only or s.id in only]

    # Approvals are automatic here so the suite can run unattended. The approval
    # gate itself is covered by the test suite, not by these scenarios.
    gateway = AegisGateway(settings, approvals=AutoApproveForTesting())
    await gateway.start()

    report = EvalReport(replayed=live is None)
    skipped: list[dict[str, str]] = []
    try:
        for scenario in scenarios:
            if live is not None:
                chain = live
            elif scenario.incident_number.upper() in traces:
                chain = ProviderChain(
                    providers=[ReplayProvider(traces[scenario.incident_number.upper()], pacing=0)]
                )
            else:
                skipped.append(
                    {
                        "scenario_id": scenario.id,
                        "reason": "No model provider key and no stored trace for this incident.",
                    }
                )
                continue
            if live is None and scenario.variant in NEEDS_LIVE_MODEL:
                skipped.append(
                    {
                        "scenario_id": scenario.id,
                        "reason": (
                            "This scenario needs the agent to choose differently, which a "
                            "fixed trace cannot do. It needs a live model."
                        ),
                    }
                )
                continue

            # The rejection scenario changes the approver, not the agent, so it
            # runs under replay perfectly well.
            gateway.approvals = (
                RejectEverything()
                if scenario.variant == "reject every approval"
                else AutoApproveForTesting()
            )
            gateway.workflow._approvals = gateway.approvals

            print(f"running {scenario.id} ...", flush=True)
            outcome = await run_scenario(
                scenario,
                gateway.server,
                chain,
                reseed=_reseed,
                seed_incident=_seed_incident(gateway),
            )
            report.outcomes.append(outcome)
            mark = "pass" if outcome.score.passed else "FAIL"
            print(
                f"  {mark}  score {outcome.score.fraction:.2f}  {outcome.score.as_dict()['failed_checks']}"
            )
    finally:
        await gateway.stop()

    if report.outcomes:
        report.provider = report.outcomes[0].provider
        report.model = report.outcomes[0].model

    payload = report.as_dict()
    payload["skipped"] = skipped
    print("\n" + json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))

    if save and report.outcomes:
        run_id = store(report, label=provider or ("replay" if report.replayed else "live"))
        print(f"\nStored as eval run {run_id}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Aegis eval scenarios")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--only", nargs="*", default=None, help="scenario ids")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.provider, args.only, not args.no_save))


if __name__ == "__main__":
    raise SystemExit(main())

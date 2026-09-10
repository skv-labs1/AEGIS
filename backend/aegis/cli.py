"""Command line for the parts of Aegis a human drives.

Until the console exists, this is how an approver answers a proposal that the
agent is waiting on. It is deliberately a separate process from the gateway:
the person approving is not the agent asking.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .db.models import AuditEvent, Investigation, Proposal
from .db.session import init_engine, session_scope
from .governance.approvals import decide
from .governance.policy import RiskPolicy


def _pending() -> list[Proposal]:
    with session_scope() as db:
        return db.query(Proposal).filter(Proposal.state == "pending").order_by(Proposal.id).all()


def cmd_pending(_: argparse.Namespace) -> int:
    rows = _pending()
    if not rows:
        print("No proposals are waiting for a decision.")
        return 0
    print(f"{len(rows)} proposal(s) awaiting approval:\n")
    for p in rows:
        print(f"  Proposal {p.id}  [{p.policy_risk_class}]  {p.action}")
        print(f"    arguments:  {json.dumps(p.arguments)}")
        print(f"    why:        {p.rationale}")
        print(f"    expected:   {p.expected_outcome}")
        if p.agent_risk_assessment:
            print(f"    agent risk: {p.agent_risk_assessment} ({p.agent_risk_rationale or ''})")
        print(f"    approve:    aegis approve {p.id} --approver 'Your Name' --role it_admin")
        print(
            f"    reject:     aegis reject {p.id} --approver 'Your Name' --role it_admin"
            " --note 'reason'\n"
        )
    return 0


def _decide(args: argparse.Namespace, approve: bool) -> int:
    policy = RiskPolicy.load(get_settings().policy_file)
    try:
        result = decide(
            args.proposal_id,
            approve=approve,
            approver=args.approver,
            role=args.role,
            note=args.note,
            policy=policy,
        )
    except (KeyError, ValueError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = "approved" if approve else "rejected"
    print(f"Proposal {result['proposal_id']} {verb} by {args.approver} ({args.role}).")
    print(f"  action: {result['action']}  risk: {result['policy_risk_class']}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _decide(args, approve=True)


def cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args, approve=False)


def cmd_investigations(args: argparse.Namespace) -> int:
    with session_scope() as db:
        rows = db.query(Investigation).order_by(Investigation.id.desc()).limit(args.limit).all()
        if not rows:
            print("No investigations yet.")
            return 0
        for r in rows:
            print(f"  #{r.id}  {r.incident_number:<10} {r.state:<20} {r.root_cause or ''}"[:120])
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    with session_scope() as db:
        query = db.query(AuditEvent).order_by(AuditEvent.id)
        if args.investigation:
            query = query.filter(AuditEvent.investigation_id == args.investigation)
        events = query.limit(args.limit).all()
    if args.json:
        print(json.dumps([e.as_dict() for e in events], indent=2))
        return 0
    for e in events:
        stamp = e.occurred_at.strftime("%H:%M:%S") if e.occurred_at else "--:--:--"
        decision = f"[{e.policy_decision}]" if e.policy_decision else ""
        print(f"  {stamp} {e.actor:<8} {e.event_type:<20} {e.tool_name or '':<32} {decision}")
        if e.summary:
            print(f"           {e.summary}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="aegis", description="Aegis operator commands")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pending", help="list proposals awaiting a decision").set_defaults(
        func=cmd_pending
    )

    for name, handler, help_text in (
        ("approve", cmd_approve, "approve a proposal"),
        ("reject", cmd_reject, "reject a proposal"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("proposal_id", type=int)
        p.add_argument("--approver", required=True, help="who is deciding")
        p.add_argument("--role", required=True, help="service_desk | it_admin | auditor")
        p.add_argument("--note", default=None, help="why")
        p.set_defaults(func=handler)

    p = sub.add_parser("investigations", help="list recent investigations")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_investigations)

    p = sub.add_parser("audit", help="print the audit trail")
    p.add_argument("--investigation", type=int, default=None)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    init_engine(get_settings())
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

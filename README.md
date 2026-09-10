# Aegis — Agentic IT Operations

Aegis is an **MCP server that governs agentic IT operations**. Any MCP-capable agent (Claude Code,
Claude Desktop, or your own) connects to Aegis and investigates and remediates incidents through it.
Aegis enforces the workflow, classifies risk, gates writes behind human approval, verifies outcomes,
and records a full audit trail. Enterprise systems (ITSM, ITAM, endpoint management) sit behind Aegis
as their own MCP servers.

The agent gathers evidence from multiple enterprise systems, correlates it, records a diagnosis with
cited evidence, proposes a remediation, waits for human approval where policy requires it, executes
the approved action, verifies the result against before/after telemetry, and updates the incident.

```text
Incident → Investigation → Evidence → Diagnosis → Recommendation → Approval → Remediation → Verification → Resolution
```

## Status

**Phase 0: architecture proposal.** No application code yet.
Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the proposed stack, the layered design, the
governance model, the evaluation approach, and the open decisions.

## What it is and is not

- It is a working demonstration of agentic investigation, tool calling, integration abstraction,
  human-in-the-loop governance, verification, and auditability.
- The repo ships demo MCP servers for ITSM, ITAM, and endpoint management backed by synthetic data.
  No vendor integration is implemented or tested; any vendor or community MCP server can be attached
  through config.
- It runs at zero token cost: the live agent is Claude Code or Claude Desktop on a personal plan, and
  the hosted demo replays recorded runs without calling a model.
- It is not a replacement for an ITSM platform.

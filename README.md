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

**Phase 2 complete: the governance gateway runs.** Aegis connects to three demo enterprise
systems, re-publishes their tools under its own namespace, and puts every call through
policy, an approval gate, an audit trail and evidence capture. 70 tests pass across both
packages.

Phases 3 onward (the agent engine, the console, evaluation) are still proposal. Read
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and
[docs/MCP_CONTRACT.md](docs/MCP_CONTRACT.md) for the tool contract.

## Quick start

```bash
make install   # two virtualenvs: the demo systems and the gateway
make stack     # start the demo systems and the gateway in the background
make test      # 70 tests across both packages
make stop
```

The gateway serves MCP at `http://127.0.0.1:8800/mcp`. The demo enterprise systems sit
behind it on ports 8801 to 8803 and are not meant to be reached directly by an agent.

### Attaching an agent

`.mcp.json` in the repo root already points Claude Code at the gateway, so `make stack`
followed by starting Claude Code in this directory is enough to investigate INC-1042
through governed tools. Any MCP host works, including MCP Inspector
(`npx @modelcontextprotocol/inspector`).

Note that with no approval channel attached, every write is refused by design. Approvals
arrive with the console in a later phase.

## What the gateway does

```text
agent host ──MCP──▶ Aegis gateway ──MCP──▶ itsm | itam | endpoint
                    │
                    policy check → approval gate → audit → call → evidence
```

- **Tools are namespaced by source system**, so `get_incident` becomes `itsm_get_incident`
  and two systems cannot collide.
- **Denied actions are never published.** `endpoint_reimage_device` exists upstream and the
  agent never sees it, because advertising an action it may never take only invites an attempt.
- **A tool Aegis has never seen defaults to safe.** Reads are allowed and logged so a newly
  attached system does not block an investigation. Anything that does not positively declare
  itself read-only is treated as a write and refused until an administrator classifies it.
- **The audit table is append-only**, enforced in code rather than by convention.
- **Evidence is kept, master data is not.** Aegis stores what a tool returned during an
  investigation, because the live record will have moved on by the time anyone reviews the
  decision. Users, devices, assets and tickets stay in the systems of record.

## The demo scenario

Incident INC-1042: a finance executive reports a slow laptop and repeated Outlook crashes.
The evidence is spread across all three systems and only makes sense when correlated.

- The device is at 97% disk, with 128 GB reclaimable. The single largest item is 62 GB of
  orphaned Outlook data files, so the disk problem is itself Outlook's doing.
- Two critical patches are missing, and both failed to download with
  `0x80070070 - insufficient disk space`. Patch non-compliance is a symptom, not a cause.
- The installed Office build is 1198 days old and carries a documented defect: Outlook
  crashes when its data file exceeds available disk space.
- The caller has four earlier Outlook incidents, every one closed as a workaround.
- Health has fallen 42 points over 14 days. This was gradual, not sudden.

Remediation moves real state: health 31.7 to 76.3, disk 97% to 72%, crashes 4 to 0. The
device stays at "fair" rather than "healthy" because the outdated Office build and missing
patches remain, which is the honest outcome and the reason a second, higher-risk action
needs its own approval.

## What it is and is not

- It is a working demonstration of agentic investigation, tool calling, integration abstraction,
  human-in-the-loop governance, verification, and auditability.
- The repo ships demo MCP servers for ITSM, ITAM, and endpoint management backed by synthetic data.
  No vendor integration is implemented or tested; any vendor or community MCP server can be attached
  through config.
- It runs at zero token cost. The built-in engine uses free-tier providers (Gemini Flash, then Groq)
  behind a provider abstraction, so a paid provider is a config change. Claude Code on a personal
  plan is a second host. Replay mode runs recorded traces with no model at all.
- It is not a replacement for an ITSM platform.

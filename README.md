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

**Phase 5 complete: the console runs the whole thing.** Aegis governs three demo enterprise
systems, holds every device-changing action behind a proposal a named human approves,
verifies remediation against before and after measurements, and shows all of it live in an
operations console. A built-in engine can drive investigations on a free-tier model, and any
MCP host such as Claude Code can drive them instead. 123 tests pass across both packages.

Phases 6 onward (replay mode, evaluation, deployment) are still proposal. Read
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and
[docs/MCP_CONTRACT.md](docs/MCP_CONTRACT.md) for the tool contract.

## Quick start

```bash
make install   # Python venvs and the console build
make demo      # everything, in the background
make test      # 123 tests across both packages
make stop
```

Then open **http://127.0.0.1:8000**.

The console, the API and the MCP gateway are one process on port 8000. The demo enterprise
systems sit behind the gateway on ports 8801 to 8803 and are not meant to be reached
directly by an agent.

### Driving an investigation

Three ways, all governed identically:

1. **From the console.** Open an incident and press *Start AI investigation*. This needs the
   built-in engine, so set `GEMINI_API_KEY` or `GROQ_API_KEY` first.
2. **From Claude Code.** `.mcp.json` already points at `http://127.0.0.1:8000/mcp`, so
   starting Claude Code in this directory gives it the governed tools. The console shows the
   run live either way, because the live view is a tail of the audit trail.
3. **By hand.** The device page has a *Run an action* panel that raises the same kind of
   proposal a person must approve.

Approvals appear in the console. `make pending` and `make approve ID=1 WHO="Your Name"` do
the same thing from a terminal.

When the agent proposes a remediation, its call blocks until a person decides. Answer it
from a second terminal:

```bash
make pending
make approve ID=1 WHO="Your Name" NOTE="approved during the change window"
```

`make audit` prints the trail afterwards.

## What the gateway does

```text
agent host ──MCP──▶ Aegis gateway ──MCP──▶ itsm | itam | endpoint
                    │
                    state machine → policy → approval gate → audit → call → evidence
```

A live run, with the numbers the demo actually produces:

```text
investigation 1  state=investigating
device DEV-4411  health 31.7 (critical)

propose before diagnosing →  refused: "Record a diagnosis first: a remediation
                             must follow from a stated root cause."
diagnosis recorded
proposing ... blocks until a human decides
approved by Rebecca Lindqvist (it_admin)
executed: reclaimed 128.0 GB

verification: agent=resolved  measured=resolved  agreed=True
   health_score              31.7 -> 68.3
   telemetry.disk_used_pct   97.0 -> 72.0
   telemetry.cpu_avg_pct     88.0 -> 50.4
   band                      critical -> degraded

resolved as Solved (Workaround); incident updated
```

- **The workflow is enforced, not requested.** An investigation moves
  `investigating → diagnosed → proposed → awaiting approval → approved → executed →
  verified → resolved`, and every operation checks the recorded state first. Asking to
  propose a fix before recording a diagnosis returns an error saying what is missing.
- **Device-changing actions are not callable.** They exist only as proposals. The agent sees
  what they do and what arguments they take through `list_remediation_actions`, but the only
  path to running one is a proposal a named human approved.
- **Verification is measured.** Aegis snapshots device health before and after and compares
  the agent's verdict against the numbers. An agent claiming a better outcome than the
  measurements support has the discrepancy recorded and cannot resolve the incident as fixed.
  Being more cautious than the numbers is recorded but not penalised.
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

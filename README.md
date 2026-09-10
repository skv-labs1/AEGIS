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

**Phase 1 complete: the demo enterprise systems run.** Three MCP servers (ITSM, ITAM, endpoint)
serve synthetic data over Streamable HTTP, with a device simulator so remediation genuinely
changes state. 38 tests pass, including the full headline scenario end to end over MCP.

Phases 2 onward (the Aegis gateway, the agent engine, the console) are still proposal.
Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and
[docs/MCP_CONTRACT.md](docs/MCP_CONTRACT.md) for the tool contract.

## Quick start

```bash
make install        # create the venv and install the demo systems
make demo-systems   # seed the database and run all three MCP servers
make test           # 38 tests, including the headline scenario
```

The servers listen on `http://127.0.0.1:8801/mcp` (ITSM), `:8802/mcp` (ITAM) and
`:8803/mcp` (endpoint). Point any MCP client at them, including MCP Inspector
(`npx @modelcontextprotocol/inspector`).

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

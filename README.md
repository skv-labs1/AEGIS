# Aegis — Agentic IT Operations

Aegis is a prototype of an AI-native layer that sits across existing IT systems (ITSM, ITAM, endpoint
management) and orchestrates incident investigation and governed remediation.

An analyst starts an AI investigation on an incident. The agent gathers evidence from multiple
enterprise systems through controlled tools, correlates it, explains the probable root cause,
recommends a remediation, classifies its risk, waits for human approval where policy requires it,
executes the approved action, verifies the result, and updates the incident. Every step is audited.

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
- Enterprise systems are reached over the Model Context Protocol (MCP). The repo ships demo MCP
  servers for ITSM, ITAM, and endpoint management backed by synthetic data. No vendor integration is
  implemented or tested; any vendor or community MCP server can be attached through config.
- It is not a replacement for an ITSM platform.

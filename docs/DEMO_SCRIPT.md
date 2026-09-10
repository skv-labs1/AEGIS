# Aegis demo script

Ten minutes, one browser tab, no API key required. The numbers below are what the
demo actually produces, so you can rehearse against them.

## Before you start

```bash
make demo          # everything, backgrounded
```

Open http://127.0.0.1:8000. If you have run the demo before, press **Reset demo** in
the top right so INC-1042 is fresh.

Have a second terminal ready if you want to show the operator commands.

---

## 1. The problem, in the queue (30 seconds)

Open the **Incidents** page.

> "This is a service desk queue. Six open incidents. The one I care about is INC-1042:
> a VP of Finance saying her laptop is slow and Outlook keeps crashing. It is the most
> common ticket in enterprise IT and the least satisfying, because the usual fix is to
> restart something and close it."

Point at the resolved list below: **four earlier Outlook incidents from the same
caller**, over 79 days, every one closed as a workaround.

> "That is the real problem. Nobody ever found the cause."

---

## 2. Start the investigation (1 minute)

Open INC-1042. Point out the description panel labelled **untrusted input**.

> "The ticket text is something a person wrote. Aegis hands it to the agent explicitly
> marked as data, never as instructions. That matters in a minute."

Press **Replay investigation** (or **Start AI investigation** if you have a key set).

> "With no API key this replays the agent's decisions from a stored trace. Everything
> else is real: the same policy checks, the same approval gate, the same verification.
> Only the model call is replaced."

Watch the **Live investigation** panel on the right. It is a tail of the audit trail,
not a separate feed, so the demo and the record cannot disagree.

---

## 3. The diagnosis (2 minutes)

When the diagnosis card appears, read the root cause, then walk the contributing factors.
The point to land:

> "The disk is 97% full. The single largest thing on it is 62 GB of orphaned Outlook
> data files from mailboxes that no longer exist. So Outlook caused its own disk problem,
> and the full disk is what stops Outlook growing its data file and crashes it.
>
> Two critical patches are missing. Both failed with the same error code: insufficient
> disk space. Patch non-compliance here is a symptom, not a separate fault.
>
> And the installed Office build carries a documented defect where Outlook crashes when
> its data file exceeds available disk space. The symptom matches exactly.
>
> No single system tells you this. It only appears when you correlate four of them."

---

## 4. The approval gate (2 minutes)

The investigation stops at **awaiting approval** and the timeline says the agent is blocked.

> "The agent cannot run this. Device-changing actions are not callable through Aegis at
> all. They exist only as proposals a named human approves."

Show the policy view for the credibility beat:

- Go to **Policy**. Show `endpoint_clear_disk_space` marked **proposal only**, and
  `endpoint_reimage_device` marked **deny**.
- Show the roles table: an **Auditor** may approve nothing.

> "This is a YAML file, not prompt text. The agent cannot see it, argue with it, or
> lower a classification. It can state its own opinion of the risk, which gets recorded
> next to the policy's, and the policy's decides."

Switch the persona picker to **Priya Raghavan, Auditor** and try to approve. It is refused.
Switch back to **Rebecca Lindqvist, IT Administrator** and approve.

---

## 5. Verification (2 minutes)

The remediation runs and the verification panel appears:

| Measure | Before | After |
|---|---|---|
| Health score | 31.7 | 68.3 |
| Disk used | 97% | 72% |
| CPU average | 88% | 50.4% |

> "Aegis snapshots the device before and after and compares the agent's own verdict
> against the measurement. If the agent claimed this was resolved when the numbers had
> not moved, the discrepancy would be recorded and it would be blocked from closing the
> incident as fixed."

Note the honest outcome:

> "It closes as a workaround, not a permanent fix, because the outdated Office build is
> still there. That residual risk needs a separate, higher-risk approval. An agent that
> declared total victory here would be wrong, and the system would catch it."

---

## 6. The audit trail (1 minute)

Open **Audit**.

> "Every tool call, every policy decision, every approval, with who did it and how long
> it took. Append-only: a correction is another event, never an edit. This is the same
> data the live view was reading."

---

## 7. The architecture point (2 minutes)

Open **Agent activity** and show the three connected systems.

> "Aegis is an MCP server. The agent connects to it, and it connects to the enterprise
> systems, which are themselves MCP servers. Swapping the demo ITSM for ServiceNow is
> one line of config; the credentials live in that server, never in Aegis.
>
> And the agent is replaceable. This ran on a stored trace. It also runs on Gemini or
> Groq through the built-in engine, and it runs from Claude Code, which connects to the
> same endpoint. All three get identical governance, because the governance is not in
> the agent."

If you have Claude Code to hand, this is the moment: `.mcp.json` already points at the
gateway, so you can start it in the repo and watch the same console fill up.

---

## 8. Close

> "The demo is synthetic data end to end and no vendor system is connected. What is real
> is the shape: an AI-native governance layer between agents and the systems they act on,
> where investigation is cheap and autonomous, and action is gated, verified and audited."

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| Queue is empty | The demo systems did not start. `make stop && make demo`, then check `/tmp/aegis-demo.log`. |
| Button says nothing happens | An investigation already exists for that incident. Press **Reset demo**. |
| "No stored trace" | You picked an incident other than INC-1042. Only that one ships with a trace. |
| Live run fails | Check the key with `make check-providers`. Replay always works without one. |

## What to say if asked what is not real

- No vendor ITSM, CMDB or endpoint platform is connected. The three systems behind the
  gateway are demo servers over synthetic data.
- Remediation changes a simulated device, not a real endpoint.
- The shipped trace for INC-1042 is authored by hand, not recorded from a model. The
  console says so wherever replay is used, and `make record-trace` replaces it with a
  real recording once a provider key is available.

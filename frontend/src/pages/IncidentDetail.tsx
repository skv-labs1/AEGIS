import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  subscribe,
  type AuditEvent,
  type IncidentDetail as IncidentPayload,
  type InvestigationDetail,
  type Status,
} from "../lib/api";
import {
  Button,
  Card,
  Chip,
  Empty,
  ErrorNote,
  Mono,
  RiskChip,
  Spinner,
  StateChip,
  Untrusted,
  clockTime,
  relativeTime,
} from "../components/ui";

const PHASE_ORDER = [
  "investigating",
  "diagnosed",
  "proposed",
  "awaiting_approval",
  "approved",
  "executed",
  "verified",
  "resolved",
];

export function IncidentDetail({
  number,
  onOpenDevice,
  status,
}: {
  number: string;
  onOpenDevice: (id: string) => void;
  status: Status | null;
}) {
  const [incident, setIncident] = useState<IncidentPayload | null>(null);
  const [investigation, setInvestigation] = useState<InvestigationDetail | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [mode, setMode] = useState<string | null>(null);
  const investigationId = investigation?.investigation_id ?? incident?.investigation?.investigation_id ?? null;
  const lastRun = status?.last_runs?.[number.toUpperCase()];
  const trace = status?.replay?.traces?.[number.toUpperCase()];
  const canReplay = Boolean(trace);
  const traceNote = trace
    ? `${trace.turns} turns, ${trace.origin === "recorded" ? `recorded from ${trace.provider}/${trace.model}` : "authored by hand, not a recording"}`
    : "";
  const replaying = mode === "replay" || lastRun?.replayed;

  const load = useCallback(async () => {
    try {
      const detail = await api.incident(number);
      setIncident(detail);
      const id = detail.investigation?.investigation_id;
      if (id) {
        const full = await api.investigation(id);
        setInvestigation(full);
        setEvents(full.events);
      }
      setError(null);
    } catch (exc) {
      setError(String(exc));
    }
  }, [number]);

  useEffect(() => {
    setIncident(null);
    setInvestigation(null);
    setEvents([]);
    load();
  }, [number, load]);

  // Live tail of the audit trail, filtered to this investigation.
  useEffect(() => {
    const stop = subscribe((event) => {
      if (investigationId && event.investigation_id === investigationId) {
        setEvents((current) => (current.some((e) => e.id === event.id) ? current : [...current, event]));
      }
      if (event.event_type === "run.completed" || event.phase === "resolve" || event.event_type === "approval.required") {
        load();
      }
    });
    return stop;
  }, [investigationId, load]);

  // While a run is in flight the investigation record changes underneath us.
  useEffect(() => {
    if (!investigationId) return;
    const timer = setInterval(async () => {
      try {
        const full = await api.investigation(investigationId);
        setInvestigation(full);
        setEvents((current) => (full.events.length > current.length ? full.events : current));
      } catch {
        /* transient */
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [investigationId]);

  async function startInvestigation(mode: "auto" | "live" | "replay" = "auto") {
    setStarting(true);
    setError(null);
    setMode(null);
    try {
      const result = await api.investigate(number, mode);
      if (!result.started) setError(result.reason ?? "Could not start.");
      else setMode(result.mode ?? null);
      setTimeout(load, 1200);
    } catch (exc) {
      setError(String(exc));
    } finally {
      setStarting(false);
    }
  }

  if (!incident) return <Spinner label="Loading incident" />;
  const detail = incident.incident;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Mono>{detail.number}</Mono>
            <StateChip state={detail.state} />
            {detail.caller?.vip && <Chip tone="bg-warn-500/15 text-warn-500 ring-warn-500/30">VIP caller</Chip>}
          </div>
          <h1 className="mt-1 text-lg font-semibold">{detail.short_description}</h1>
          <p className="text-xs text-ink-400">
            {detail.caller?.display_name} · {detail.caller?.job_title} · opened {relativeTime(detail.opened_at)}
            {detail.device_id && (
              <>
                {" · "}
                <button onClick={() => onOpenDevice(detail.device_id!)} className="text-signal-400 hover:underline">
                  {detail.device_id}
                </button>
              </>
            )}
          </p>
        </div>
        {!investigationId && detail.state !== "resolved" && (
          <div className="flex items-center gap-2">
            {canReplay && !status?.engine.available && (
              <Chip tone="bg-violet-500/15 text-violet-500 ring-violet-500/30" title={traceNote}>
                replay available
              </Chip>
            )}
            <Button
              variant="primary"
              onClick={() => startInvestigation("auto")}
              disabled={starting || (!status?.engine.available && !canReplay)}
              title={
                status?.engine.available
                  ? "Runs on the configured model provider"
                  : canReplay
                    ? "No model provider configured, so this replays a stored trace. The gateway, approvals and verification still run for real."
                    : undefined
              }
            >
              {starting ? "Starting" : status?.engine.available ? "Start AI investigation" : "Replay investigation"}
            </Button>
          </div>
        )}
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
      {!status?.engine.available && !investigationId && (
        <div className="rounded-md border border-ink-700 bg-ink-850 px-3 py-2 text-xs text-ink-300">
          {status?.engine.reason ?? "The built-in engine has no model provider available."}
          {canReplay && (
            <>
              {" "}
              A stored trace exists for this incident, so pressing the button replays the agent's
              decisions ({traceNote}). Everything else runs for real: the same policy checks, the
              same approval gate, and verification measured against the device.
            </>
          )}{" "}
          The live view tails the audit trail either way, so a run driven from Claude Code appears
          here as it happens.
        </div>
      )}
      {replaying && (
        <div className="rounded-md border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs text-violet-500">
          Replay: the agent's decisions come from a stored trace
          {trace?.origin === "authored" ? ", authored by hand rather than recorded from a model" : ""}.
          The gateway, policy, approval and verification are running for real.
        </div>
      )}
      {lastRun && lastRun.finished !== "completed" && (
        <ErrorNote>
          The last engine run for this incident ended as {lastRun.finished}.
          {lastRun.error ? ` ${lastRun.error}` : ""}
        </ErrorNote>
      )}

      {investigation && <Progress state={investigation.state} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          {investigation?.diagnosis && <Diagnosis investigation={investigation} />}
          {investigation && <Proposals investigation={investigation} onChanged={load} />}
          <Card title="What the user reported">
            {detail.description ? (
              <Untrusted text={detail.description.text} source={detail.description.source} />
            ) : (
              <Empty>No description.</Empty>
            )}
          </Card>
          {detail.work_notes && detail.work_notes.length > 0 && (
            <Card title={`Work notes (${detail.work_notes.length})`}>
              <ul className="space-y-2">
                {detail.work_notes.map((note, index) => (
                  <li key={index} className="rounded-md border border-ink-800 bg-ink-850 px-3 py-2">
                    <div className="mb-1 flex items-center gap-2 text-[11px] text-ink-400">
                      <span className="text-ink-200">{note.author}</span>
                      <span>{relativeTime(note.created_at)}</span>
                    </div>
                    <p className="text-sm whitespace-pre-wrap text-ink-200">{note.text.text}</p>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Timeline events={events} />
          {investigation && investigation.evidence.length > 0 && <EvidencePanel investigation={investigation} />}
        </div>
      </div>
    </div>
  );
}

function Progress({ state }: { state: string }) {
  const failed = state === "verification_failed" || state === "escalated" || state === "rejected";
  const index = PHASE_ORDER.indexOf(state);
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-ink-800 bg-ink-900 px-3 py-2.5">
      {PHASE_ORDER.map((phase, position) => {
        const reached = index >= position;
        const current = index === position;
        return (
          <div key={phase} className="flex items-center gap-1.5">
            <span
              className={`rounded px-1.5 py-0.5 text-[11px] whitespace-nowrap ${
                current
                  ? "bg-signal-500 text-white pulse"
                  : reached
                    ? "bg-ok-500/15 text-ok-500"
                    : "bg-ink-850 text-ink-400"
              }`}
            >
              {phase.replace(/_/g, " ")}
            </span>
            {position < PHASE_ORDER.length - 1 && <span className="text-ink-700">›</span>}
          </div>
        );
      })}
      {failed && <span className="ml-2"><StateChip state={state} /></span>}
    </div>
  );
}

function Diagnosis({ investigation }: { investigation: InvestigationDetail }) {
  const diagnosis = investigation.diagnosis!;
  return (
    <Card
      title="Diagnosis"
      subtitle="Recorded by the agent, with the evidence it relied on."
      actions={<Chip>confidence: {diagnosis.confidence ?? "unstated"}</Chip>}
    >
      <p className="text-sm text-ink-100">{diagnosis.root_cause}</p>
      {diagnosis.contributing_factors && diagnosis.contributing_factors.length > 0 && (
        <>
          <h3 className="mt-3 text-[11px] uppercase tracking-wide text-ink-400">Contributing factors</h3>
          <ul className="mt-1 space-y-1">
            {diagnosis.contributing_factors.map((factor, index) => (
              <li key={index} className="flex gap-2 text-sm text-ink-200">
                <span className="text-ink-600">•</span>
                {factor}
              </li>
            ))}
          </ul>
        </>
      )}
      {diagnosis.evidence_cited && diagnosis.evidence_cited.length > 0 && (
        <>
          <h3 className="mt-3 text-[11px] uppercase tracking-wide text-ink-400">Evidence cited</h3>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {diagnosis.evidence_cited.map((item) => (
              <Chip key={item}>{item}</Chip>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

function Proposals({ investigation, onChanged }: { investigation: InvestigationDetail; onChanged: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(id: number, approve: boolean) {
    setBusy(id);
    setError(null);
    try {
      await api.decide(id, approve, approve ? "Approved from the console" : "Rejected from the console");
      onChanged();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(null);
    }
  }

  if (investigation.proposals.length === 0) return null;

  return (
    <>
      {investigation.proposals.map((proposal) => {
        const verification = investigation.verifications.find((v) => v.proposal_id === proposal.proposal_id);
        return (
          <Card
            key={proposal.proposal_id}
            title={
              <span className="flex items-center gap-2">
                Remediation <Mono>{proposal.action}</Mono>
              </span>
            }
            subtitle={proposal.rationale}
            actions={
              <>
                <RiskChip risk={proposal.policy_risk_class} />
                <StateChip state={proposal.state} />
              </>
            }
          >
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-[11px] uppercase tracking-wide text-ink-400">Expected outcome</dt>
                <dd className="text-ink-200">{proposal.expected_outcome}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wide text-ink-400">Arguments</dt>
                <dd><Mono>{JSON.stringify(proposal.arguments)}</Mono></dd>
              </div>
              {proposal.agent_risk_assessment && (
                <div>
                  <dt className="text-[11px] uppercase tracking-wide text-ink-400">Agent's own risk view</dt>
                  <dd className="text-ink-200">
                    {proposal.agent_risk_assessment}
                    <span className="ml-2 text-xs text-ink-400">policy classification decides</span>
                  </dd>
                </div>
              )}
              {proposal.approved_by && (
                <div>
                  <dt className="text-[11px] uppercase tracking-wide text-ink-400">Decided by</dt>
                  <dd className="text-ink-200">
                    {proposal.approved_by} <span className="text-ink-400">({proposal.approver_role})</span>
                  </dd>
                </div>
              )}
            </dl>

            {proposal.state === "pending" && (
              <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-warn-500/30 bg-warn-500/5 px-3 py-2">
                <span className="text-xs text-warn-500">
                  Waiting for approval from {proposal.policy?.approver_roles.join(" or ") ?? "an authorised role"}.
                  The agent is blocked until someone decides.
                </span>
                <div className="ml-auto flex gap-2">
                  <Button variant="danger" disabled={busy === proposal.proposal_id} onClick={() => decide(proposal.proposal_id, false)}>
                    Reject
                  </Button>
                  <Button variant="primary" disabled={busy === proposal.proposal_id} onClick={() => decide(proposal.proposal_id, true)}>
                    Approve
                  </Button>
                </div>
              </div>
            )}

            {verification && <Verification record={verification} />}
          </Card>
        );
      })}
      {error && <ErrorNote>{error}</ErrorNote>}
    </>
  );
}

function Verification({ record }: { record: NonNullable<InvestigationDetail["verifications"][number]> }) {
  const metrics = record.measured_delta?.metrics ?? [];
  return (
    <div className={`mt-3 rounded-md border px-3 py-2 ${record.agreed ? "border-ok-500/30 bg-ok-500/5" : "border-alert-500/40 bg-alert-500/5"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-ink-100">Verification</span>
        <Chip tone="bg-ink-700 text-ink-200 ring-ink-600">agent said {record.agent_verdict}</Chip>
        <Chip tone={record.agreed ? "bg-ok-500/15 text-ok-500 ring-ok-500/30" : "bg-alert-500/15 text-alert-500 ring-alert-500/30"}>
          measured {record.measured_verdict}
        </Chip>
      </div>
      {metrics.length > 0 && (
        <table className="mt-2 w-full text-left text-xs">
          <tbody>
            {metrics.map((metric) => (
              <tr key={metric.metric} className="border-t border-ink-800/60">
                <td className="py-1 pr-3 text-ink-400">{metric.metric.replace("telemetry.", "")}</td>
                <td className="py-1 pr-2 tabular-nums text-ink-300">{String(metric.before)}</td>
                <td className="py-1 pr-2 text-ink-600">→</td>
                <td className={`py-1 tabular-nums ${metric.improved === false ? "text-alert-500" : "text-ok-500"}`}>
                  {String(metric.after)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {record.discrepancy && (
        <p className={`mt-2 text-xs ${record.agreed ? "text-ink-400" : "text-alert-500"}`}>{record.discrepancy}</p>
      )}
    </div>
  );
}

function EvidencePanel({ investigation }: { investigation: InvestigationDetail }) {
  return (
    <Card title={`Evidence (${investigation.evidence.length})`} subtitle="What the agent actually saw, kept for review.">
      <ul className="space-y-1.5">
        {investigation.evidence.map((item) => (
          <li key={item.id} className="flex items-start gap-2 text-xs">
            <Chip>{item.source_system}</Chip>
            <div className="min-w-0">
              <Mono>{item.tool}</Mono>
              {item.subject && <span className="ml-1.5 text-ink-400">{item.subject}</span>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Timeline({ events }: { events: AuditEvent[] }) {
  const scroller = useRef<HTMLDivElement>(null);
  const ordered = useMemo(() => [...events].sort((a, b) => a.id - b.id), [events]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [ordered.length]);

  return (
    <Card title="Live investigation" subtitle="A tail of the audit trail, as it is written.">
      {ordered.length === 0 ? (
        <Empty>Nothing yet. Start an investigation to watch it happen.</Empty>
      ) : (
        <div ref={scroller} className="max-h-[520px] space-y-1.5 overflow-y-auto pr-1">
          {ordered.map((event) => (
            <TimelineRow key={event.id} event={event} />
          ))}
        </div>
      )}
    </Card>
  );
}

const ACTOR_TONE: Record<string, string> = {
  agent: "bg-signal-500/15 text-signal-400 ring-signal-500/30",
  gateway: "bg-violet-500/15 text-violet-500 ring-violet-500/30",
  human: "bg-ok-500/15 text-ok-500 ring-ok-500/30",
  system: "bg-ink-700 text-ink-300 ring-ink-600",
};

function TimelineRow({ event }: { event: AuditEvent }) {
  const refused = event.event_type === "policy.refused" || Boolean(event.error);
  return (
    <div className={`animate-in rounded-md border px-2.5 py-1.5 ${refused ? "border-alert-500/30 bg-alert-500/5" : "border-ink-800 bg-ink-850"}`}>
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className="font-mono text-ink-400">{clockTime(event.occurred_at)}</span>
        <Chip tone={ACTOR_TONE[event.actor]}>{event.actor_label ?? event.actor}</Chip>
        <span className="text-ink-300">{event.event_type.replace(/\./g, " ")}</span>
        {event.risk_class && event.risk_class !== "READ" && <RiskChip risk={event.risk_class} />}
        {event.latency_ms != null && <span className="ml-auto text-ink-600">{event.latency_ms.toFixed(0)}ms</span>}
      </div>
      {event.tool && (
        <div className="mt-0.5">
          <Mono>{event.tool}</Mono>
          {event.upstream && <span className="ml-1.5 text-[11px] text-ink-400">via {event.upstream}</span>}
        </div>
      )}
      {event.summary && <p className="mt-0.5 text-xs text-ink-300">{event.summary}</p>}
      {event.error && <p className="mt-0.5 text-xs text-alert-500">{event.error}</p>}
    </div>
  );
}

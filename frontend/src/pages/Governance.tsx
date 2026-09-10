import { useEffect, useState } from "react";
import { api, subscribe, type AuditEvent, type Metrics, type PolicyView, type Status } from "../lib/api";
import { Card, Chip, Empty, ErrorNote, Mono, RiskChip, Spinner, clockTime } from "../components/ui";

export function Policy() {
  const [policy, setPolicy] = useState<PolicyView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.policy().then(setPolicy).catch((exc) => setError(String(exc)));
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!policy) return <Spinner label="Loading policy" />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Risk policy</h1>
        <p className="text-xs text-ink-400">
          Policy is data, not prompt text. The agent cannot see it, argue with it, or lower a
          classification. Version {policy.version}.
        </p>
      </div>

      <Card title="Roles" subtitle="Who may approve what.">
        <div className="flex flex-wrap gap-3">
          {Object.entries(policy.roles).map(([name, role]) => (
            <div key={name} className="rounded-md border border-ink-800 bg-ink-850 px-3 py-2">
              <div className="text-sm text-ink-100">{role.label}</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {role.may_approve.length === 0 ? (
                  <Chip tone="bg-ink-700 text-ink-400 ring-ink-600">approves nothing</Chip>
                ) : (
                  role.may_approve.map((risk) => <RiskChip key={risk} risk={risk} />)
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="Tools Aegis has never seen"
        subtitle="What happens when a new system is attached before anyone classifies it."
      >
        <div className="flex flex-wrap gap-4 text-xs">
          <div>
            <div className="text-ink-400">Reads</div>
            <Chip tone="bg-ok-500/15 text-ok-500 ring-ok-500/30">{policy.unclassified_defaults.read}</Chip>
          </div>
          <div>
            <div className="text-ink-400">Writes</div>
            <Chip tone="bg-alert-500/15 text-alert-500 ring-alert-500/30">{policy.unclassified_defaults.write}</Chip>
          </div>
        </div>
        <p className="mt-2 text-xs text-ink-300">{policy.unclassified_defaults.reason}</p>
      </Card>

      <Card title={`Classified tools (${policy.rules.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-ink-800 text-[11px] uppercase tracking-wide text-ink-400">
                <th className="py-2 pr-3 font-medium">Tool</th>
                <th className="py-2 pr-3 font-medium">Risk</th>
                <th className="py-2 pr-3 font-medium">Decision</th>
                <th className="py-2 pr-3 font-medium">Reachable how</th>
                <th className="py-2 font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {policy.rules.map((rule) => (
                <tr key={rule.tool} className="border-b border-ink-850 last:border-0">
                  <td className="py-2 pr-3"><Mono>{rule.tool}</Mono></td>
                  <td className="py-2 pr-3"><RiskChip risk={rule.risk_class} /></td>
                  <td className="py-2 pr-3">
                    <Chip
                      tone={
                        rule.decision === "deny"
                          ? "bg-alert-500/15 text-alert-500 ring-alert-500/30"
                          : rule.decision === "require_approval"
                            ? "bg-warn-500/15 text-warn-500 ring-warn-500/30"
                            : "bg-ok-500/15 text-ok-500 ring-ok-500/30"
                      }
                    >
                      {rule.decision.replace(/_/g, " ")}
                    </Chip>
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    {rule.published && <Chip>callable</Chip>}
                    {rule.proposal_only && <Chip tone="bg-violet-500/15 text-violet-500 ring-violet-500/30">proposal only</Chip>}
                    {rule.workflow_managed && <Chip tone="bg-ink-700 text-ink-300 ring-ink-600">workflow only</Chip>}
                    {!rule.published && !rule.proposal_only && !rule.workflow_managed && (
                      <Chip tone="bg-alert-500/15 text-alert-500 ring-alert-500/30">not offered</Chip>
                    )}
                  </td>
                  <td className="py-2 text-xs text-ink-400">{rule.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

export function AgentActivity({ status }: { status: Status | null }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.audit().then((r) => setEvents(r.events)).catch((exc) => setError(String(exc)));
    return subscribe((event) => setEvents((current) => [...current, event]));
  }, []);

  const modelCalls = events.filter((e) => e.event_type === "model.called");
  const toolCalls = events.filter((e) => e.event_type === "tool.called");
  const refusals = events.filter((e) => e.event_type === "policy.refused");
  const approvals = events.filter((e) => e.event_type === "approval.required");

  const byTool = new Map<string, number>();
  for (const event of toolCalls) {
    if (event.tool) byTool.set(event.tool, (byTool.get(event.tool) ?? 0) + 1);
  }
  const ranked = [...byTool.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
  const latencies = toolCalls.map((e) => e.latency_ms ?? 0).filter(Boolean).sort((a, b) => a - b);
  const median = latencies.length ? latencies[Math.floor(latencies.length / 2)] : 0;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Agent activity</h1>
        <p className="text-xs text-ink-400">Everything the connected hosts have done through the gateway.</p>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Tool calls" value={toolCalls.length} />
        <Stat label="Model calls" value={modelCalls.length} />
        <Stat label="Approvals raised" value={approvals.length} />
        <Stat label="Refused by policy" value={refusals.length} tone={refusals.length ? "text-warn-500" : undefined} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Connected systems">
          <ul className="space-y-1.5 text-xs">
            {(status?.systems ?? []).map((system) => (
              <li key={system.name} className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${system.connected ? "bg-ok-500" : "bg-alert-500"}`} />
                <span className="text-ink-200">{system.name}</span>
                <Mono>{system.url}</Mono>
                <span className="ml-auto text-ink-400">{system.tool_count} tools</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 text-xs text-ink-400">
            Median tool latency {median.toFixed(0)}ms · engine{" "}
            {status?.engine.available ? status.engine.providers.join(" → ") : "not configured"}
          </div>
        </Card>

        <Card title="Most used tools">
          {ranked.length === 0 ? (
            <Empty>No tool calls yet.</Empty>
          ) : (
            <ul className="space-y-1">
              {ranked.map(([tool, count]) => (
                <li key={tool} className="flex items-center gap-2 text-xs">
                  <Mono>{tool}</Mono>
                  <div className="ml-auto flex items-center gap-2">
                    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-ink-800">
                      <div
                        className="h-full rounded-full bg-signal-500"
                        style={{ width: `${(count / ranked[0][1]) * 100}%` }}
                      />
                    </div>
                    <span className="w-6 text-right tabular-nums text-ink-300">{count}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export function Audit() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.audit().then((r) => setEvents(r.events)).catch((exc) => setError(String(exc)));
    return subscribe((event) => setEvents((current) => [...current, event]));
  }, []);

  const shown = events.filter((event) => {
    if (!filter) return true;
    const haystack = `${event.event_type} ${event.tool ?? ""} ${event.actor} ${event.actor_label ?? ""} ${event.summary ?? ""}`;
    return haystack.toLowerCase().includes(filter.toLowerCase());
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Audit trail</h1>
          <p className="text-xs text-ink-400">
            Append-only. A correction is another event, never an edit. {events.length} recorded.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by tool, actor or event"
            className="w-64 rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-100"
          />
          <a
            href="/api/audit?limit=1000"
            target="_blank"
            rel="noreferrer"
            className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-xs text-ink-200 hover:bg-ink-700"
          >
            Export JSON
          </a>
        </div>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}

      <Card>
        {shown.length === 0 ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead>
                <tr className="border-b border-ink-800 text-[11px] uppercase tracking-wide text-ink-400">
                  <th className="py-2 pr-3 font-medium">Time</th>
                  <th className="py-2 pr-3 font-medium">Actor</th>
                  <th className="py-2 pr-3 font-medium">Event</th>
                  <th className="py-2 pr-3 font-medium">Tool</th>
                  <th className="py-2 pr-3 font-medium">System</th>
                  <th className="py-2 pr-3 font-medium">Risk</th>
                  <th className="py-2 pr-3 font-medium">Decision</th>
                  <th className="py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((event) => (
                  <tr key={event.id} className="border-b border-ink-850 last:border-0 hover:bg-ink-850">
                    <td className="py-1.5 pr-3 font-mono text-ink-400">{clockTime(event.occurred_at)}</td>
                    <td className="py-1.5 pr-3 text-ink-200">{event.actor_label ?? event.actor}</td>
                    <td className="py-1.5 pr-3 text-ink-300">{event.event_type}</td>
                    <td className="py-1.5 pr-3"><Mono>{event.tool ?? ""}</Mono></td>
                    <td className="py-1.5 pr-3 text-ink-400">{event.upstream ?? ""}</td>
                    <td className="py-1.5 pr-3">{event.risk_class && <RiskChip risk={event.risk_class} />}</td>
                    <td className="py-1.5 pr-3 text-ink-400">{event.policy_decision ?? ""}</td>
                    <td className="py-1.5 text-ink-300">
                      {event.summary}
                      {event.error && <div className="text-alert-500">{event.error}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 px-3 py-2.5">
      <div className={`text-2xl font-semibold tabular-nums ${tone ?? "text-ink-100"}`}>{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-ink-400">{label}</div>
    </div>
  );
}


export function MetricsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => api.metrics().then(setMetrics).catch((exc) => setError(String(exc)));
    load();
    const timer = setInterval(load, 8000);
    return () => clearInterval(timer);
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!metrics) return <Spinner label="Loading metrics" />;

  const run = metrics.latest_run;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Evaluation</h1>
        <p className="text-xs text-ink-400">
          Scenarios are scored from the audit trail, never from the agent's account of itself.
          {" "}{metrics.scenarios_defined} scenarios defined.
        </p>
      </div>

      {run?.replayed && (
        <div className="rounded-md border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs text-violet-500">
          The latest run used replayed traces. That measures the harness and the governance, not a
          model. Set a provider key and run <Mono>make evals</Mono> for results that say anything
          about model quality.
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Scenarios defined" value={metrics.scenarios_defined} />
        <Stat label="Passed in last run" value={run?.passed ?? 0} />
        <Stat label="Tool calls seen" value={metrics.operations.tool_calls} />
        <Stat
          label="Refused by policy"
          value={metrics.operations.refusals}
          tone={metrics.operations.refusals ? "text-warn-500" : undefined}
        />
      </div>

      {run ? (
        <Card
          title={`Latest run — ${run.provider}/${run.model}`}
          subtitle={`Mean score ${(run.mean_score * 100).toFixed(0)}% across ${run.scenarios} scenario(s)`}
          actions={run.replayed ? <Chip tone="bg-violet-500/15 text-violet-500 ring-violet-500/30">replayed</Chip> : <Chip>live</Chip>}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-ink-800 text-[11px] uppercase tracking-wide text-ink-400">
                  <th className="py-2 pr-3 font-medium">Scenario</th>
                  <th className="py-2 pr-3 font-medium">Result</th>
                  <th className="py-2 pr-3 font-medium">Score</th>
                  <th className="py-2 pr-3 font-medium">Turns</th>
                  <th className="py-2 font-medium">Failed checks</th>
                </tr>
              </thead>
              <tbody>
                {metrics.latest_results.map((result) => (
                  <tr key={result.scenario_id} className="border-b border-ink-850 last:border-0">
                    <td className="py-2 pr-3">
                      <div className="text-ink-100">{result.title}</div>
                      <Mono>{result.scenario_id}</Mono>
                    </td>
                    <td className="py-2 pr-3">
                      <Chip tone={result.passed ? "bg-ok-500/15 text-ok-500 ring-ok-500/30" : "bg-alert-500/15 text-alert-500 ring-alert-500/30"}>
                        {result.passed ? "pass" : "fail"}
                      </Chip>
                    </td>
                    <td className="py-2 pr-3 tabular-nums text-ink-200">{(result.score * 100).toFixed(0)}%</td>
                    <td className="py-2 pr-3 tabular-nums text-ink-300">{result.turns}</td>
                    <td className="py-2 text-xs text-alert-500">{result.failed_checks.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : (
        <Card title="No evaluation has been run">
          <Empty>
            Run <Mono>make evals</Mono> to score the scenarios and populate this page.
          </Empty>
        </Card>
      )}

      {Object.keys(metrics.by_provider).length > 0 && (
        <Card title="Model usage by provider" subtitle="Recorded from the audit trail.">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-ink-800 text-[11px] uppercase tracking-wide text-ink-400">
                <th className="py-2 pr-3 font-medium">Provider</th>
                <th className="py-2 pr-3 font-medium">Calls</th>
                <th className="py-2 pr-3 font-medium">Input tokens</th>
                <th className="py-2 font-medium">Output tokens</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(metrics.by_provider).map(([name, usage]) => (
                <tr key={name} className="border-b border-ink-850 last:border-0">
                  <td className="py-1.5 pr-3"><Mono>{name}</Mono></td>
                  <td className="py-1.5 pr-3 tabular-nums text-ink-200">{usage.calls}</td>
                  <td className="py-1.5 pr-3 tabular-nums text-ink-300">{usage.input_tokens}</td>
                  <td className="py-1.5 tabular-nums text-ink-300">{usage.output_tokens}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

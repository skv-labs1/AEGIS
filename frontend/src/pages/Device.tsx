import { useCallback, useEffect, useState } from "react";
import { api, type DeviceDetail as DevicePayload, type Status } from "../lib/api";
import {
  Button,
  Card,
  Chip,
  Empty,
  ErrorNote,
  HealthDial,
  Mono,
  RiskChip,
  Sparkline,
  Spinner,
  StateChip,
  relativeTime,
} from "../components/ui";

export function Device({ deviceId, status, onOpenIncident }: { deviceId: string; status: Status | null; onOpenIncident: (n: string) => void }) {
  const [data, setData] = useState<DevicePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.device(deviceId));
      setError(null);
    } catch (exc) {
      setError(String(exc));
    }
  }, [deviceId]);

  useEffect(() => {
    setData(null);
    load();
  }, [deviceId, load]);

  async function raise(action: string) {
    setBusy(action);
    setNotice(null);
    setError(null);
    try {
      const proposal = await api.manualAction(deviceId, action, "Raised by hand from the device page.");
      setNotice(
        proposal.approval_required
          ? `Proposal ${proposal.proposal_id} raised and is waiting for approval. A person clicking a button gets the same gate the agent does.`
          : `Proposal ${proposal.proposal_id} was permitted without approval.`,
      );
      load();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(null);
    }
  }

  if (!data) return error ? <ErrorNote>{error}</ErrorNote> : <Spinner label="Loading device" />;

  const { device, health, software, patches, history, incidents, asset } = data;
  const actions = status?.remediation_actions.filter((a) => a.action.startsWith("endpoint_")) ?? [];

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <Mono>{device.device_id}</Mono>
          <StateChip state={device.compliance_state} />
        </div>
        <h1 className="mt-1 text-lg font-semibold">{device.hostname}</h1>
        <p className="text-xs text-ink-400">
          {device.manufacturer} {device.model} · {device.os_name} {device.os_version} ·{" "}
          {device.primary_user?.display_name ?? "unassigned"}
        </p>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
      {notice && (
        <div className="rounded-md border border-signal-500/30 bg-signal-500/10 px-3 py-2 text-xs text-signal-400">
          {notice}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card title="Health">
            <HealthDial score={health.health_score} band={health.band} />
            <div className="mt-3">
              <div className="text-[11px] uppercase tracking-wide text-ink-400">14 day trend</div>
              <Sparkline points={history.points.map((p) => p.health_score)} />
              <div className="text-[11px] text-ink-400">
                {history.trend.direction} · {history.trend.health_score_change} points
              </div>
            </div>
          </Card>

          <Card title="Why the score is what it is">
            {health.penalties.length === 0 ? (
              <Empty>No penalties. Fully healthy.</Empty>
            ) : (
              <ul className="space-y-1.5">
                {health.penalties.map((penalty) => (
                  <li key={penalty.component} className="text-xs">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-ink-200">{penalty.component.replace(/_/g, " ")}</span>
                      <span className="tabular-nums text-alert-500">-{penalty.penalty}</span>
                    </div>
                    <div className="text-ink-400">{penalty.measurement}</div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {asset && (
            <Card title="Asset">
              <dl className="space-y-1.5 text-xs">
                <Row label="Tag" value={asset.asset_tag} />
                <Row label="Warranty" value={asset.support_position?.replace(/_/g, " ")} />
                <Row label="Refresh" value={asset.refresh_position?.replace(/_/g, " ")} />
                <Row label="Cost centre" value={asset.cost_centre} />
              </dl>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card
            title="Run an action by hand"
            subtitle="The same policy, approval and audit as when the agent asks."
          >
            {actions.length === 0 ? (
              <Empty>No remediation actions available.</Empty>
            ) : (
              <div className="flex flex-wrap gap-2">
                {actions.map((action) => (
                  <div key={action.action} className="rounded-md border border-ink-800 bg-ink-850 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <Mono>{action.action.replace("endpoint_", "")}</Mono>
                      <RiskChip risk={action.risk_class} />
                    </div>
                    <p className="mt-1 max-w-xs text-[11px] text-ink-400">{action.expected_effect}</p>
                    <div className="mt-2">
                      <Button disabled={busy === action.action} onClick={() => raise(action.action)}>
                        {busy === action.action ? "Raising" : action.approval_required ? "Propose" : "Run"}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title={`Reclaimable disk (${health.reclaimable_disk.total_gb} GB)`}>
            {health.reclaimable_disk.categories.length === 0 ? (
              <Empty>Nothing reclaimable.</Empty>
            ) : (
              <ul className="space-y-1">
                {health.reclaimable_disk.categories.map((category) => (
                  <li key={category.category} className="flex items-baseline gap-2 text-xs">
                    <span className="w-16 shrink-0 tabular-nums text-ink-200">{category.gb} GB</span>
                    <span className="text-ink-300">{category.category.replace(/_/g, " ")}</span>
                    <span className="truncate text-ink-400">{category.detail}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title={`Patches — ${patches.missing_count} missing`}>
            {patches.missing.length === 0 ? (
              <Empty>Fully patched.</Empty>
            ) : (
              <>
                <ul className="space-y-1.5">
                  {patches.missing.map((patch: any) => (
                    <li key={patch.patch_id} className="text-xs">
                      <div className="flex items-center gap-2">
                        <Mono>{patch.patch_id}</Mono>
                        <Chip tone={patch.severity === "critical" ? "bg-alert-500/15 text-alert-500 ring-alert-500/30" : undefined}>
                          {patch.severity}
                        </Chip>
                      </div>
                      <div className="text-ink-300">{patch.title}</div>
                      {patch.failure_reason && <div className="text-alert-500">{patch.failure_reason}</div>}
                    </li>
                  ))}
                </ul>
                {patches.common_failure_reasons.length > 0 && (
                  <p className="mt-2 rounded border-l-2 border-warn-500 bg-warn-500/5 py-1 pl-2 text-xs text-warn-500">
                    Every failure gives the same reason. That points at a cause elsewhere on the device.
                  </p>
                )}
              </>
            )}
          </Card>

          <Card title={`Software (${software.count})`}>
            <ul className="space-y-1">
              {software.software.map((item: any) => (
                <li key={item.name} className="flex flex-wrap items-baseline gap-2 text-xs">
                  <span className="text-ink-200">{item.name}</span>
                  <Mono>{item.installed_version}</Mono>
                  {item.is_outdated && (
                    <Chip tone="bg-warn-500/15 text-warn-500 ring-warn-500/30">
                      outdated → {item.latest_available_version}
                    </Chip>
                  )}
                  {item.known_issues?.length > 0 && (
                    <Chip tone="bg-alert-500/15 text-alert-500 ring-alert-500/30">
                      {item.known_issues.length} known issue{item.known_issues.length > 1 ? "s" : ""}
                    </Chip>
                  )}
                </li>
              ))}
            </ul>
          </Card>

          <Card title={`Incident history (${incidents.count})`}>
            {incidents.count === 0 ? (
              <Empty>No incidents for this device.</Empty>
            ) : (
              <>
                <ul className="space-y-1">
                  {incidents.incidents.map((incident) => (
                    <li key={incident.number}>
                      <button
                        onClick={() => onOpenIncident(incident.number)}
                        className="flex w-full items-center gap-2 rounded px-1 py-1 text-left text-xs hover:bg-ink-850"
                      >
                        <Mono>{incident.number}</Mono>
                        <span className="flex-1 truncate text-ink-200">{incident.short_description}</span>
                        {incident.resolution_code?.includes("Workaround") && (
                          <Chip tone="bg-warn-500/15 text-warn-500 ring-warn-500/30">workaround</Chip>
                        )}
                        <span className="text-ink-400">{relativeTime(incident.opened_at)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {incidents.summary.note && (
                  <p className="mt-2 rounded border-l-2 border-warn-500 bg-warn-500/5 py-1 pl-2 text-xs text-warn-500">
                    {incidents.summary.note}
                  </p>
                )}
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-ink-400">{label}</dt>
      <dd className="text-right text-ink-200">{String(value ?? "—")}</dd>
    </div>
  );
}

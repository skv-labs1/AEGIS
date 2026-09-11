import { useEffect, useState } from "react";
import { api, type Dashboard as DashboardData, type Status } from "../lib/api";
import {
  BarList,
  Card,
  Chip,
  Empty,
  ErrorNote,
  Mono,
  Spinner,
  StatTile,
  relativeTime,
} from "../components/ui";

/**
 * The landing view: the estate as it stands, and what Aegis has done about it.
 *
 * The top half is read from the connected ITSM, ITAM and endpoint systems
 * through the gateway, so it is policy-classified and audited like any other
 * tool call. Point the gateway at a real ITSM and this half changes with no
 * code change here. The bottom half is Aegis's own record — approvals,
 * verification, refusals — which no vendor system can supply.
 */
export function Dashboard({
  status,
  onOpenDevice,
  onOpenIncident,
  onOpenIncidents,
  onOpenApprovals,
}: {
  status: Status | null;
  onOpenDevice: (id: string) => void;
  onOpenIncident: (number: string) => void;
  onOpenIncidents: () => void;
  onOpenApprovals: () => void;
}) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      api
        .dashboard()
        .then((result) => {
          setData(result);
          setError(null);
        })
        .catch((exc) => setError(String(exc)));
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, []);

  if (error && !data) return <ErrorNote>{error}</ErrorNote>;
  if (!data) return <Spinner label="Reading the estate through the gateway" />;

  const { fleet, incidents, assets, governance } = data;
  const bands = fleet.health.bands;
  const atRisk = (bands.degraded ?? 0) + (bands.critical ?? 0);
  const workarounds = incidents.resolution_quality;
  const repeats = incidents.repeats;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Operations</h1>
          <p className="text-xs text-ink-400">
            {fleet.device_count} managed devices, {incidents.total} incidents and {assets.total} assets,
            read through the Aegis gateway from {status?.systems.length ?? 3} connected systems.
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-ink-500">
          {error && <span className="text-warn-500">refresh failed · showing last good read</span>}
          <span>updated {relativeTime(data.generated_at)}</span>
        </div>
      </div>

      {/* Three figures any service desk already tracks, then three only a
          governed agent platform can report. */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="Open incidents"
          value={incidents.open}
          detail={`${incidents.opened_recently.count} raised in ${incidents.opened_recently.days}d`}
          onClick={onOpenIncidents}
        />
        <StatTile
          label="Devices at risk"
          value={atRisk}
          tone={atRisk > 0 ? "text-warn-500" : "text-ok-500"}
          detail={`${bands.critical ?? 0} critical · ${bands.degraded ?? 0} degraded`}
          title={fleet.health.band_thresholds}
        />
        <StatTile
          label="Missing critical patches"
          value={fleet.patching.missing_critical_count}
          tone={fleet.patching.missing_critical_count > 0 ? "text-alert-500" : "text-ok-500"}
          detail={`across ${fleet.patching.devices_missing_critical} devices`}
        />
        <StatTile
          label="Awaiting approval"
          value={governance.approvals.pending}
          tone={governance.approvals.pending > 0 ? "text-warn-500" : undefined}
          detail="agent is blocked until a human decides"
          onClick={onOpenApprovals}
        />
        <StatTile
          label="Verified remediations"
          value={governance.verification.confirmed}
          tone={governance.verification.confirmed > 0 ? "text-ok-500" : undefined}
          detail={
            governance.verification.disagreements > 0
              ? `${governance.verification.disagreements} agent claims corrected`
              : `measured, not claimed · ${governance.verification.total} checked`
          }
        />
        <StatTile
          label="Repeat incidents"
          value={repeats.incidents_on_repeat_devices}
          tone={repeats.incidents_on_repeat_devices > 0 ? "text-violet-500" : undefined}
          detail={`on ${repeats.devices_affected} devices that keep coming back`}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Card
          title="Patch estate"
          subtitle="Most exposed updates and why they are not installing"
          className="lg:col-span-2"
        >
          {fleet.patching.top_missing_updates.length === 0 ? (
            <Empty>Every scanned device is fully patched.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-[11px] uppercase tracking-wide text-ink-500">
                  <tr>
                    <th className="pb-2 pr-3 font-medium">Update</th>
                    <th className="pb-2 pr-3 font-medium">Severity</th>
                    <th className="pb-2 pr-3 text-right font-medium">Devices</th>
                    <th className="pb-2 font-medium">Leading failure reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-850">
                  {fleet.patching.top_missing_updates.map((patch) => {
                    const leading = Object.entries(patch.failure_reasons).sort((a, b) => b[1] - a[1])[0];
                    return (
                      <tr key={patch.patch_id} className="align-top">
                        <td className="py-2 pr-3">
                          <Mono>{patch.patch_id}</Mono>
                          <div className="mt-0.5 max-w-[22rem] truncate text-ink-400">{patch.title}</div>
                        </td>
                        <td className="py-2 pr-3">
                          <Chip
                            tone={
                              patch.severity === "critical"
                                ? "bg-alert-500/15 text-alert-500 ring-alert-500/30"
                                : "bg-warn-500/15 text-warn-500 ring-warn-500/30"
                            }
                          >
                            {patch.severity ?? "unrated"}
                          </Chip>
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-100">
                          {patch.devices_missing}
                        </td>
                        <td className="py-2 text-ink-400">
                          {leading ? (
                            <>
                              <Mono>{leading[0]}</Mono>
                              <span className="ml-1.5 text-ink-500">×{leading[1]}</span>
                            </>
                          ) : (
                            <span className="text-ink-600">pending install, no error</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-3 text-[11px] text-ink-500">
                A failure reason often names the real fault. Disk-space errors here are the same
                condition that stops an application from saving.
              </p>
            </div>
          )}
        </Card>

        <Card title="Investigations" subtitle="Where each one currently stands in the workflow">
          {governance.investigations.total === 0 ? (
            <Empty>No investigation has run yet. Open an incident and start one.</Empty>
          ) : (
            <Funnel
              rows={governance.investigations.funnel}
              total={governance.investigations.total}
            />
          )}
          <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-ink-850 pt-3 text-xs">
            <Figure label="Approved" value={governance.approvals.approved} />
            <Figure label="Rejected" value={governance.approvals.rejected} />
            <Figure
              label="Refused by policy"
              value={governance.policy.refusals}
              tone={governance.policy.refusals > 0 ? "text-alert-500" : undefined}
            />
            <Figure label="Governed tool calls" value={governance.policy.governed_tool_calls} />
          </dl>
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Card title="Fleet health" subtitle={fleet.health.band_thresholds}>
          <BarList
            total={fleet.scored_device_count}
            items={[
              { label: "Healthy", value: bands.healthy ?? 0, tone: "bg-ok-500" },
              { label: "Fair", value: bands.fair ?? 0, tone: "bg-signal-500" },
              { label: "Degraded", value: bands.degraded ?? 0, tone: "bg-warn-500" },
              { label: "Critical", value: bands.critical ?? 0, tone: "bg-alert-500" },
            ]}
          />
          <div className="mt-3 border-t border-ink-850 pt-3">
            <div className="mb-2 text-[11px] uppercase tracking-wide text-ink-500">
              Operating systems
            </div>
            <BarList
              total={fleet.device_count}
              items={Object.entries(fleet.operating_systems).map(([label, value]) => ({
                label,
                value,
                tone: "bg-ink-600",
              }))}
            />
          </div>
        </Card>

        <Card
          title="Devices needing attention"
          subtitle="Lowest health first, with the measurement costing the most"
        >
          {fleet.devices_needing_attention.length === 0 ? (
            <Empty>Nothing below the healthy band.</Empty>
          ) : (
            <ul className="divide-y divide-ink-850">
              {fleet.devices_needing_attention.slice(0, 6).map((device) => (
                <li key={device.device_id}>
                  <button
                    onClick={() => onOpenDevice(device.device_id)}
                    className="flex w-full items-start justify-between gap-3 py-2 text-left hover:bg-ink-850"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-xs text-ink-100">{device.hostname}</span>
                      <span className="mt-0.5 block truncate text-[11px] text-ink-500">
                        {device.largest_penalty
                          ? `${device.largest_penalty.component.replace(/_/g, " ")} · ${device.largest_penalty.measurement}`
                          : device.device_id}
                      </span>
                    </span>
                    <span
                      className={`shrink-0 text-sm font-semibold tabular-nums ${
                        device.band === "critical"
                          ? "text-alert-500"
                          : device.band === "degraded"
                            ? "text-warn-500"
                            : "text-signal-400"
                      }`}
                    >
                      {device.health_score.toFixed(1)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Closed without a fix"
          subtitle={`${workarounds.resolved_as_workaround} of ${workarounds.resolved} resolved incidents`}
        >
          <div className="mb-3 flex items-baseline gap-2">
            <span className="text-2xl font-semibold tabular-nums text-warn-500">
              {workarounds.workaround_rate_pct ?? 0}%
            </span>
            <span className="text-[11px] text-ink-400">closed with a workaround</span>
          </div>
          {workarounds.examples.length === 0 ? (
            <Empty>Every resolved incident addressed a cause.</Empty>
          ) : (
            <ul className="divide-y divide-ink-850">
              {workarounds.examples.slice(0, 5).map((incident) => (
                <li key={incident.number}>
                  <button
                    onClick={() => onOpenIncident(incident.number)}
                    className="flex w-full items-baseline justify-between gap-3 py-1.5 text-left hover:bg-ink-850"
                  >
                    <span className="min-w-0">
                      <Mono>{incident.number}</Mono>
                      <span className="mt-0.5 block truncate text-[11px] text-ink-400">
                        {incident.short_description}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] text-ink-500">
                      {incident.resolved_days_ago === null
                        ? "just now"
                        : incident.resolved_days_ago === 0
                          ? "today"
                          : `${incident.resolved_days_ago}d ago`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[11px] text-ink-500">{workarounds.note}</p>
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Card
          title="Repeat callers"
          subtitle={`${repeats.callers_affected} people have raised more than one incident`}
        >
          {incidents.repeat_callers.length === 0 ? (
            <Empty>No caller has raised more than one incident.</Empty>
          ) : (
            <ul className="divide-y divide-ink-850">
              {incidents.repeat_callers.slice(0, 5).map((caller) => (
                <li key={caller.caller_id} className="flex items-baseline justify-between gap-3 py-2">
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate text-xs text-ink-100">{caller.display_name}</span>
                      {caller.vip && <Chip tone="bg-violet-500/15 text-violet-500 ring-violet-500/30">VIP</Chip>}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-ink-500">
                      {caller.department}
                    </span>
                  </span>
                  <span className="shrink-0 text-right text-[11px] text-ink-400">
                    <span className="text-ink-100">{caller.incidents}</span> incidents
                    <span className="block text-ink-500">{caller.workarounds} workarounds</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Asset position" subtitle="What the estate is worth supporting">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5 text-xs">
            <Figure label="In warranty" value={assets.warranty.in_warranty} tone="text-ok-500" />
            <Figure
              label="Out of warranty"
              value={assets.warranty.out_of_warranty}
              tone={assets.warranty.out_of_warranty > 0 ? "text-warn-500" : undefined}
            />
            <Figure
              label={`Expiring in ${assets.warranty.expiring_within_days}d`}
              value={assets.warranty.expiring_soon}
            />
            <Figure
              label="Overdue for refresh"
              value={assets.refresh.overdue}
              tone={assets.refresh.overdue > 0 ? "text-warn-500" : undefined}
            />
            <Figure
              label="Spares in stock"
              value={Object.values(assets.spares_by_subcategory).reduce((a, b) => a + b, 0)}
            />
            <Figure label="Average age" value={`${assets.refresh.average_age_days ?? 0}d`} />
          </dl>
          <div className="mt-3 border-t border-ink-850 pt-3">
            <div className="mb-2 text-[11px] uppercase tracking-wide text-ink-500">By lifecycle</div>
            <BarList
              total={assets.total}
              items={Object.entries(assets.by_lifecycle_state).map(([label, value]) => ({
                label: label.replace(/_/g, " "),
                value,
                tone: label === "in_use" ? "bg-signal-500" : "bg-ink-600",
              }))}
            />
          </div>
        </Card>

        <Card title="Connected systems" subtitle="Every figure above came through one of these">
          <ul className="space-y-2">
            {(status?.systems ?? []).map((system) => (
              <li
                key={system.name}
                className="flex items-center justify-between gap-3 rounded-md border border-ink-800 bg-ink-850 px-3 py-2"
              >
                <span className="flex items-center gap-2">
                  <span className={`h-1.5 w-1.5 rounded-full ${system.connected ? "bg-ok-500" : "bg-alert-500"}`} />
                  <span className="text-xs text-ink-100">{system.name}</span>
                </span>
                <span className="text-[11px] text-ink-500">
                  {system.connected ? `${system.tool_count} tools` : (system.error ?? "unreachable")}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[11px] text-ink-500">
            Each is an MCP server. Swapping one for a vendor's own server is a config change, not a
            code change — every read above still runs through the same policy and audit path.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Chip>synthetic data</Chip>
            <Chip tone="bg-ink-700 text-ink-200 ring-ink-600">
              policy v{status?.policy_version ?? "—"}
            </Chip>
            <Chip tone="bg-ink-700 text-ink-200 ring-ink-600">
              {status?.published_tools ?? 0} governed tools
            </Chip>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Funnel({ rows, total }: { rows: { state: string; count: number }[]; total: number }) {
  const widest = Math.max(...rows.map((r) => r.count), 1);
  return (
    <ol className="space-y-1.5">
      {rows.map((row) => (
        <li key={row.state} className="flex items-center gap-2.5">
          <span className="w-[8.5rem] shrink-0 text-[11px] text-ink-400">
            {row.state.replace(/_/g, " ")}
          </span>
          <span className="h-5 flex-1 overflow-hidden rounded bg-ink-850">
            <span
              className={`block h-full rounded ${
                row.state === "resolved" || row.state === "verified" ? "bg-ok-500/60" : "bg-signal-500/50"
              }`}
              style={{ width: `${Math.max((row.count / widest) * 100, row.count ? 6 : 0)}%` }}
            />
          </span>
          <span className="w-6 shrink-0 text-right text-xs tabular-nums text-ink-200">{row.count}</span>
        </li>
      ))}
      <li className="pt-1 text-[11px] text-ink-500">
        {total} investigation{total === 1 ? "" : "s"} total, each shown at the state it has reached. A
        state is only entered by satisfying the one before it.
      </li>
    </ol>
  );
}

function Figure({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-500">{label}</dt>
      <dd className={`text-sm font-semibold tabular-nums ${tone ?? "text-ink-100"}`}>{value}</dd>
    </div>
  );
}

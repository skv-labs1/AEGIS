import { useEffect, useState } from "react";
import { api, type Incident, type Status } from "../lib/api";
import { Button, Card, Empty, ErrorNote, Mono, Spinner, StateChip, relativeTime } from "../components/ui";

const PRIORITY_LABEL: Record<number, string> = { 1: "Critical", 2: "High", 3: "Moderate", 4: "Low" };

export function Incidents({ onOpen, status }: { onOpen: (n: string) => void; status: Status | null }) {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const result = await api.incidents();
      setIncidents(result.incidents);
      setError(null);
    } catch (exc) {
      setError(String(exc));
    }
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, []);

  const open = (incidents ?? []).filter((i) => i.state !== "resolved");
  const closed = (incidents ?? []).filter((i) => i.state === "resolved");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Incident queue</h1>
          <p className="text-xs text-ink-400">
            Live from the service management system through the Aegis gateway.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          Report an incident
        </Button>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
      {creating && (
        <NewIncidentForm
          onClose={() => setCreating(false)}
          onCreated={(number) => {
            setCreating(false);
            load();
            onOpen(number);
          }}
          engineAvailable={status?.engine.available ?? false}
        />
      )}

      <Card title={`Open (${open.length})`}>
        {incidents === null ? <Spinner /> : open.length === 0 ? <Empty>Nothing open.</Empty> : <Table rows={open} onOpen={onOpen} />}
      </Card>

      {closed.length > 0 && (
        <Card title={`Recently resolved (${closed.length})`}>
          <Table rows={closed} onOpen={onOpen} />
        </Card>
      )}
    </div>
  );
}

function Table({ rows, onOpen }: { rows: Incident[]; onOpen: (n: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead>
          <tr className="border-b border-ink-800 text-[11px] uppercase tracking-wide text-ink-400">
            <th className="py-2 pr-3 font-medium">Number</th>
            <th className="py-2 pr-3 font-medium">Summary</th>
            <th className="py-2 pr-3 font-medium">Caller</th>
            <th className="py-2 pr-3 font-medium">Priority</th>
            <th className="py-2 pr-3 font-medium">State</th>
            <th className="py-2 pr-3 font-medium">AI</th>
            <th className="py-2 font-medium">Opened</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((incident) => (
            <tr
              key={incident.number}
              onClick={() => onOpen(incident.number)}
              className="cursor-pointer border-b border-ink-850 transition-colors last:border-0 hover:bg-ink-850"
            >
              <td className="py-2.5 pr-3"><Mono>{incident.number}</Mono></td>
              <td className="py-2.5 pr-3 text-ink-100">{incident.short_description}</td>
              <td className="py-2.5 pr-3 text-ink-300">
                {incident.caller?.display_name ?? "—"}
                {incident.caller?.vip && <span className="ml-1.5 text-[10px] text-warn-500">VIP</span>}
              </td>
              <td className="py-2.5 pr-3 text-ink-300">P{incident.priority} {PRIORITY_LABEL[incident.priority]}</td>
              <td className="py-2.5 pr-3"><StateChip state={incident.state} /></td>
              <td className="py-2.5 pr-3">
                {incident.investigation ? <StateChip state={incident.investigation.state} /> : <span className="text-xs text-ink-400">—</span>}
              </td>
              <td className="py-2.5 text-xs text-ink-400">{relativeTime(incident.opened_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CALLERS = [
  { id: "USR-1001", label: "Priya Raghavan — VP Finance", device: "DEV-4411" },
  { id: "USR-1005", label: "Aisha Bello — HR Business Partner", device: "DEV-4421" },
  { id: "USR-1007", label: "Sofia Rossi — Support Team Lead", device: "DEV-4429" },
  { id: "USR-1004", label: "Tomas Alvarez — Field Engineer", device: "DEV-4418" },
];

function NewIncidentForm({
  onClose,
  onCreated,
  engineAvailable,
}: {
  onClose: () => void;
  onCreated: (number: string) => void;
  engineAvailable: boolean;
}) {
  const [caller, setCaller] = useState(CALLERS[0]);
  const [summary, setSummary] = useState("Laptop extremely slow and Outlook keeps crashing");
  const [detail, setDetail] = useState(
    "My laptop has been getting slower for weeks and now Outlook crashes several times a day, usually when sending an attachment. I have already tried restarting it.",
  );
  const [priority, setPriority] = useState(2);
  const [auto, setAuto] = useState(engineAvailable);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.createIncident({
        short_description: summary,
        description: detail,
        caller_id: caller.id,
        device_id: caller.device,
        priority,
        investigate_automatically: auto,
      });
      onCreated(result.incident.number);
    } catch (exc) {
      setError(String(exc));
      setBusy(false);
    }
  }

  return (
    <Card
      title="Report an incident"
      subtitle="Raised through the gateway, so the ticket itself is audited."
      actions={<Button variant="ghost" onClick={onClose}>Cancel</Button>}
    >
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-ink-300">
          Caller
          <select
            value={caller.id}
            onChange={(e) => setCaller(CALLERS.find((c) => c.id === e.target.value) ?? CALLERS[0])}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100"
          >
            {CALLERS.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-ink-300">
          Priority
          <select
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100"
          >
            {[1, 2, 3, 4].map((p) => (
              <option key={p} value={p}>P{p} {PRIORITY_LABEL[p]}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-ink-300 md:col-span-2">
          Summary
          <input
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100"
          />
        </label>
        <label className="text-xs text-ink-300 md:col-span-2">
          What the user reported
          <textarea
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            rows={4}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-ink-100"
          />
        </label>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <label className={`flex items-center gap-2 text-xs ${engineAvailable ? "text-ink-300" : "text-ink-400"}`}>
          <input
            type="checkbox"
            checked={auto}
            disabled={!engineAvailable}
            onChange={(e) => setAuto(e.target.checked)}
          />
          Start an AI investigation immediately
          {!engineAvailable && <span className="text-ink-400">(no model provider configured)</span>}
        </label>
        <Button variant="primary" onClick={submit} disabled={busy}>
          {busy ? "Creating" : "Create incident"}
        </Button>
      </div>
      {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
    </Card>
  );
}

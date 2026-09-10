import { useEffect, useState } from "react";
import { PERSONAS, api, getPersona, restorePersona, setPersona, type Status } from "./lib/api";
import { Chip } from "./components/ui";
import { Incidents } from "./pages/Incidents";
import { IncidentDetail } from "./pages/IncidentDetail";
import { Approvals } from "./pages/Approvals";
import { Device } from "./pages/Device";
import { AgentActivity, Audit, Policy } from "./pages/Governance";

type View =
  | { name: "incidents" }
  | { name: "incident"; number: string }
  | { name: "approvals" }
  | { name: "device"; id: string }
  | { name: "activity" }
  | { name: "policy" }
  | { name: "audit" };

const NAV: { key: View["name"]; label: string }[] = [
  { key: "incidents", label: "Incidents" },
  { key: "approvals", label: "Approvals" },
  { key: "activity", label: "Agent activity" },
  { key: "policy", label: "Policy" },
  { key: "audit", label: "Audit" },
];

export default function App() {
  const [view, setView] = useState<View>({ name: "incidents" });
  const [status, setStatus] = useState<Status | null>(null);
  const [persona, setPersonaState] = useState(getPersona());
  const [pending, setPending] = useState(0);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    restorePersona();
    setPersonaState(getPersona());
  }, []);

  useEffect(() => {
    const load = () => {
      api.status().then(setStatus).catch(() => setStatus(null));
      api.approvals().then((r) => setPending(r.proposals.length)).catch(() => setPending(0));
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  async function resetDemo() {
    if (!confirm("Rebuild the synthetic enterprise and clear every investigation and audit record?")) return;
    setResetting(true);
    try {
      await api.reset();
      setView({ name: "incidents" });
    } finally {
      setResetting(false);
    }
  }

  const connected = status?.systems.filter((s) => s.connected).length ?? 0;
  const total = status?.systems.length ?? 0;

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 border-b border-ink-800 bg-ink-900/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3 px-4 py-2.5">
          <button onClick={() => setView({ name: "incidents" })} className="flex items-center gap-2">
            <span className="text-lg">🛡</span>
            <span className="font-semibold tracking-tight">Aegis</span>
            <span className="hidden text-xs text-ink-400 sm:inline">Agentic IT Operations</span>
          </button>

          <nav className="flex flex-wrap items-center gap-0.5">
            {NAV.map((item) => {
              const active =
                view.name === item.key ||
                (item.key === "incidents" && view.name === "incident") ||
                (item.key === "incidents" && view.name === "device");
              return (
                <button
                  key={item.key}
                  onClick={() => setView({ name: item.key } as View)}
                  className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    active ? "bg-ink-800 text-ink-100" : "text-ink-400 hover:bg-ink-850 hover:text-ink-200"
                  }`}
                >
                  {item.label}
                  {item.key === "approvals" && pending > 0 && (
                    <span className="ml-1.5 rounded-full bg-warn-500 px-1.5 text-[10px] text-ink-950">{pending}</span>
                  )}
                </button>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <span
              title={status?.systems.map((s) => `${s.name}: ${s.connected ? "connected" : s.error}`).join("\n")}
              className="hidden items-center gap-1.5 text-[11px] text-ink-400 md:flex"
            >
              <span className={`h-1.5 w-1.5 rounded-full ${connected === total && total > 0 ? "bg-ok-500" : "bg-warn-500"}`} />
              {connected}/{total} systems
            </span>
            <select
              value={persona.name}
              onChange={(event) => {
                const next = PERSONAS.find((p) => p.name === event.target.value)!;
                setPersona(next);
                setPersonaState(next);
              }}
              title="Demo identity. Not authentication; the role is still checked against the policy."
              className="rounded-md border border-ink-700 bg-ink-850 px-2 py-1 text-xs text-ink-100"
            >
              {PERSONAS.map((option) => (
                <option key={option.name} value={option.name}>
                  {option.name} — {option.label}
                </option>
              ))}
            </select>
            <button
              onClick={resetDemo}
              disabled={resetting}
              title="Rebuild the synthetic enterprise and clear Aegis's own state"
              className="rounded-md border border-ink-700 bg-ink-800 px-2 py-1 text-xs text-ink-300 hover:bg-ink-700 disabled:opacity-40"
            >
              {resetting ? "Resetting" : "Reset demo"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-4 py-5">
        {view.name === "incidents" && (
          <Incidents status={status} onOpen={(number) => setView({ name: "incident", number })} />
        )}
        {view.name === "incident" && (
          <>
            <BackLink onClick={() => setView({ name: "incidents" })}>All incidents</BackLink>
            <IncidentDetail
              number={view.number}
              status={status}
              onOpenDevice={(id) => setView({ name: "device", id })}
            />
          </>
        )}
        {view.name === "device" && (
          <>
            <BackLink onClick={() => setView({ name: "incidents" })}>All incidents</BackLink>
            <Device
              deviceId={view.id}
              status={status}
              onOpenIncident={(number) => setView({ name: "incident", number })}
            />
          </>
        )}
        {view.name === "approvals" && (
          <Approvals onOpenIncident={(number) => number && setView({ name: "incident", number })} />
        )}
        {view.name === "activity" && <AgentActivity status={status} />}
        {view.name === "policy" && <Policy />}
        {view.name === "audit" && <Audit />}
      </main>

      <footer className="mx-auto max-w-[1500px] px-4 pb-8 text-[11px] text-ink-600">
        <div className="flex flex-wrap items-center gap-2 border-t border-ink-850 pt-3">
          <Chip>synthetic data</Chip>
          <span>
            Every user, device, asset and ticket here is invented. No vendor ITSM, CMDB or endpoint
            platform is connected.
          </span>
        </div>
      </footer>
    </div>
  );
}

function BackLink({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button onClick={onClick} className="mb-3 text-xs text-ink-400 hover:text-ink-200">
      ← {children}
    </button>
  );
}

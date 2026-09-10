import { useEffect, useState } from "react";
import { api, getPersona, type Proposal } from "../lib/api";
import { Button, Card, Chip, Empty, ErrorNote, Mono, RiskChip, Spinner, StateChip, relativeTime } from "../components/ui";

export function Approvals({ onOpenIncident }: { onOpenIncident: (n: string) => void }) {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [history, setHistory] = useState<Proposal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const persona = getPersona();

  async function load() {
    try {
      const [pending, all] = await Promise.all([api.approvals(false), api.approvals(true)]);
      setProposals(pending.proposals);
      setHistory(all.proposals.filter((p) => p.state !== "pending"));
      setError(null);
    } catch (exc) {
      setError(String(exc));
    }
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  async function decide(id: number, approve: boolean) {
    setBusy(id);
    setError(null);
    try {
      await api.decide(id, approve, approve ? "Approved from the console" : "Rejected from the console");
      await load();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Approvals</h1>
        <p className="text-xs text-ink-400">
          Acting as {persona.name} ({persona.role.replace("_", " ")}). An agent proposing one of these is
          blocked until someone decides.
        </p>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}

      <Card title={`Waiting (${proposals?.length ?? 0})`}>
        {proposals === null ? (
          <Spinner />
        ) : proposals.length === 0 ? (
          <Empty>Nothing is waiting for a decision.</Empty>
        ) : (
          <div className="space-y-3">
            {proposals.map((proposal) => (
              <div key={proposal.proposal_id} className="rounded-md border border-warn-500/30 bg-warn-500/5 px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Mono>{proposal.action}</Mono>
                  <RiskChip risk={proposal.policy_risk_class} />
                  <Chip>raised by {proposal.raised_by}</Chip>
                  {proposal.investigation_id ? (
                    <Chip>investigation {proposal.investigation_id}</Chip>
                  ) : (
                    <Chip tone="bg-violet-500/15 text-violet-500 ring-violet-500/30">manual</Chip>
                  )}
                  <span className="text-[11px] text-ink-400">{relativeTime(proposal.created_at)}</span>
                </div>
                <p className="mt-1.5 text-sm text-ink-100">{proposal.rationale}</p>
                <p className="mt-0.5 text-xs text-ink-400">Expected: {proposal.expected_outcome}</p>
                <p className="mt-0.5 text-xs text-ink-400">
                  Target: <Mono>{JSON.stringify(proposal.arguments)}</Mono>
                </p>
                {proposal.policy?.reason && (
                  <p className="mt-1.5 border-l-2 border-ink-700 pl-2 text-xs text-ink-300">
                    Policy: {proposal.policy.reason}
                  </p>
                )}
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-[11px] text-ink-400">
                    Approvable by {proposal.policy?.approver_roles.join(" or ") ?? "an authorised role"}
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
              </div>
            ))}
          </div>
        )}
      </Card>

      {history.length > 0 && (
        <Card title="Decided">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead>
                <tr className="border-b border-ink-800 text-[11px] uppercase tracking-wide text-ink-400">
                  <th className="py-2 pr-3 font-medium">Action</th>
                  <th className="py-2 pr-3 font-medium">Risk</th>
                  <th className="py-2 pr-3 font-medium">Outcome</th>
                  <th className="py-2 pr-3 font-medium">Decided by</th>
                  <th className="py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody>
                {history.map((proposal) => (
                  <tr
                    key={proposal.proposal_id}
                    className="border-b border-ink-850 last:border-0 hover:bg-ink-850"
                    onClick={() => proposal.investigation_id && onOpenIncident("")}
                  >
                    <td className="py-2 pr-3"><Mono>{proposal.action}</Mono></td>
                    <td className="py-2 pr-3"><RiskChip risk={proposal.policy_risk_class} /></td>
                    <td className="py-2 pr-3"><StateChip state={proposal.state} /></td>
                    <td className="py-2 pr-3 text-ink-300">
                      {proposal.approved_by ?? "—"}
                      {proposal.approver_role && <span className="ml-1 text-xs text-ink-400">({proposal.approver_role})</span>}
                    </td>
                    <td className="py-2 text-xs text-ink-400">{relativeTime(proposal.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

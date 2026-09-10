/** Everything the console knows about the backend. */

export type Persona = { name: string; role: string; label: string };

export const PERSONAS: Persona[] = [
  { name: "Sofia Rossi", role: "service_desk", label: "Service Desk Analyst" },
  { name: "Rebecca Lindqvist", role: "it_admin", label: "IT Administrator" },
  { name: "Priya Raghavan", role: "auditor", label: "Auditor" },
];

let persona: Persona = PERSONAS[1];

export function getPersona(): Persona {
  return persona;
}

export function setPersona(next: Persona) {
  persona = next;
  try {
    localStorage.setItem("aegis.persona", next.name);
  } catch {
    /* private browsing; the picker still works for this session */
  }
}

export function restorePersona() {
  try {
    const saved = localStorage.getItem("aegis.persona");
    const found = PERSONAS.find((p) => p.name === saved);
    if (found) persona = found;
  } catch {
    /* ignore */
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Aegis-Persona": persona.name,
      "X-Aegis-Role": persona.role,
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  status: () => request<Status>("/status"),
  policy: () => request<PolicyView>("/policy"),
  incidents: (limit = 50) => request<IncidentList>(`/incidents?limit=${limit}`),
  incident: (number: string) => request<IncidentDetail>(`/incidents/${number}`),
  createIncident: (body: NewIncident) =>
    request<{ created: boolean; incident: Incident; run?: unknown }>("/incidents", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  investigate: (number: string) =>
    request<{ started: boolean; reason?: string }>(`/incidents/${number}/investigate`, {
      method: "POST",
    }),
  investigations: () => request<{ investigations: Investigation[] }>("/investigations"),
  investigation: (id: number) => request<InvestigationDetail>(`/investigations/${id}`),
  approvals: (includeDecided = false) =>
    request<{ proposals: Proposal[] }>(`/approvals?include_decided=${includeDecided}`),
  decide: (id: number, approve: boolean, note?: string) =>
    request<Record<string, unknown>>(`/approvals/${id}`, {
      method: "POST",
      body: JSON.stringify({ approve, note }),
    }),
  device: (id: string) => request<DeviceDetail>(`/devices/${id}`),
  manualAction: (deviceId: string, action: string, rationale: string) =>
    request<Proposal & { approval_required: boolean }>(`/devices/${deviceId}/actions`, {
      method: "POST",
      body: JSON.stringify({ action, arguments: {}, rationale }),
    }),
  audit: (investigationId?: number) =>
    request<{ events: AuditEvent[] }>(
      `/audit?limit=300${investigationId ? `&investigation_id=${investigationId}` : ""}`,
    ),
  reset: () => request<{ reset: boolean }>("/demo/reset", { method: "POST" }),
};

/** Subscribe to the audit trail as it is written. */
export function subscribe(onEvent: (event: AuditEvent) => void): () => void {
  const source = new EventSource("/api/stream");
  source.addEventListener("audit", (message) => {
    try {
      onEvent(JSON.parse((message as MessageEvent).data));
    } catch {
      /* a malformed frame should not kill the stream */
    }
  });
  return () => source.close();
}

// -- types --------------------------------------------------------------------

export type Untrusted = { content_type: string; source: string; guidance: string; text: string };

export type Incident = {
  number: string;
  short_description: string;
  description: Untrusted | null;
  state: string;
  priority: number;
  category: string | null;
  opened_at: string;
  resolved_at: string | null;
  resolution_code: string | null;
  device_id: string | null;
  reopen_count: number;
  caller: {
    user_id: string;
    display_name: string;
    job_title: string | null;
    department: string | null;
    location: string | null;
    vip: boolean;
  } | null;
  investigation?: { investigation_id: number; state: string } | null;
  work_notes?: { created_at: string; author: string; text: Untrusted }[];
};

export type IncidentList = {
  count: number;
  incidents: Incident[];
  summary: { resolved_as_workaround: number; note: string | null };
};

export type IncidentDetail = { found: boolean; incident: Incident; investigation: Investigation | null };

export type NewIncident = {
  short_description: string;
  description: string;
  caller_id: string;
  device_id?: string | null;
  priority: number;
  investigate_automatically: boolean;
};

export type Investigation = {
  investigation_id: number;
  incident_number: string;
  state: string;
  subject_user_id: string | null;
  subject_device_id: string | null;
  opened_at: string;
  closed_at: string | null;
  diagnosis: {
    root_cause: string;
    contributing_factors: string[] | null;
    evidence_cited: string[] | null;
    confidence: string | null;
  } | null;
  resolution: { code: string; summary: string } | null;
};

export type Proposal = {
  proposal_id: number;
  investigation_id: number | null;
  raised_by: string;
  action: string;
  arguments: Record<string, unknown>;
  rationale: string;
  expected_outcome: string;
  policy_risk_class: string;
  agent_risk_assessment: string | null;
  state: string;
  approved_by: string | null;
  approver_role: string | null;
  approval_note: string | null;
  created_at: string;
  executed_at: string | null;
  policy?: { approver_roles: string[]; reason: string };
};

export type VerificationRecord = {
  verification_id: number;
  proposal_id: number;
  agent_verdict: string;
  agent_rationale: string | null;
  measured_verdict: string;
  measured_delta: {
    primary_metric: string;
    primary_change: number | null;
    metrics: { metric: string; before: unknown; after: unknown; improvement?: number; improved?: boolean }[];
  } | null;
  agreed: boolean;
  discrepancy: string | null;
};

export type EvidenceItem = {
  id: number;
  captured_at: string;
  source_system: string;
  tool: string;
  subject: string | null;
  summary: string | null;
};

export type AuditEvent = {
  id: number;
  occurred_at: string;
  investigation_id: number | null;
  actor: string;
  actor_label: string | null;
  event_type: string;
  phase: string | null;
  tool: string | null;
  upstream: string | null;
  risk_class: string | null;
  policy_decision: string | null;
  policy_reason: string | null;
  summary: string | null;
  latency_ms: number | null;
  error: string | null;
};

export type InvestigationDetail = Investigation & {
  proposals: Proposal[];
  verifications: VerificationRecord[];
  evidence: EvidenceItem[];
  events: AuditEvent[];
  allowed_next_states: string[];
};

export type Status = {
  systems: { name: string; url: string; connected: boolean; tool_count: number; error: string | null }[];
  published_tools: number;
  tools_by_risk_class: Record<string, string[]>;
  remediation_actions: {
    action: string;
    description: string;
    risk_class: string;
    approval_required: boolean;
    approver_roles: string[];
    policy_reason: string;
    expected_effect: string | null;
  }[];
  workflow_managed: Record<string, string>;
  policy_version: number;
  engine: { available: boolean; providers: string[]; ready_providers?: string[]; reason?: string | null };
  last_runs?: Record<string, { finished: string; error?: string | null; turns?: number; tool_calls?: number; providers_used?: string[] }>;
};

export type PolicyView = {
  version: number;
  roles: Record<string, { label: string; may_approve: string[] }>;
  unclassified_defaults: { read: string; write: string; reason: string };
  rules: {
    tool: string;
    decision: string;
    risk_class: string;
    reason: string;
    approver_roles: string[];
    published: boolean;
    proposal_only: boolean;
    workflow_managed: boolean;
  }[];
};

export type DeviceDetail = {
  device: Record<string, any>;
  health: {
    health_score: number;
    band: string;
    penalties: { component: string; measurement: string; penalty: number }[];
    telemetry: Record<string, any>;
    reclaimable_disk: { total_gb: number; categories: { category: string; gb: number; detail: string }[] };
  };
  software: { count: number; software: any[]; summary: Record<string, string[]> };
  patches: { missing_count: number; missing_critical_count: number; missing: any[]; common_failure_reasons: string[] };
  history: { points: { captured_at: string; health_score: number; disk_used_pct: number }[]; trend: Record<string, any> };
  incidents: IncidentList;
  asset: Record<string, any> | null;
};

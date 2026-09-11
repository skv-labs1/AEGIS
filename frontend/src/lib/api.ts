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
  investigate: (number: string, mode: "auto" | "live" | "replay" = "auto") =>
    request<{ started: boolean; reason?: string; mode?: string; trace_origin?: string | null }>(
      `/incidents/${number}/investigate?mode=${mode}`,
      { method: "POST" },
    ),
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
  dashboard: () => request<Dashboard>("/dashboard"),
  metrics: () => request<Metrics>("/metrics"),
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
  last_runs?: Record<
    string,
    { finished: string; error?: string | null; turns?: number; tool_calls?: number; providers_used?: string[]; replayed?: boolean }
  >;
  replay?: {
    available_for: string[];
    traces: Record<string, { origin: string; provider: string; model: string; turns: number; recorded_at: string; notes: string }>;
  };
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

export type Metrics = {
  scenarios_defined: number;
  latest_run: {
    run_id: number;
    started_at: string;
    label: string;
    provider: string;
    model: string;
    replayed: boolean;
    scenarios: number;
    passed: number;
    mean_score: number;
  } | null;
  latest_results: {
    scenario_id: string;
    title: string;
    passed: boolean;
    score: number;
    turns: number;
    tool_calls: number;
    duration_ms: number;
    failed_checks: string[];
    checks: { check: string; passed: boolean; detail: string; weight: number }[];
  }[];
  history: { run_id: number; provider: string; replayed: boolean; mean_score: number; passed: number; scenarios: number }[];
  operations: {
    tool_calls: number;
    model_calls: number;
    refusals: number;
    approvals_requested: number;
    median_tool_latency_ms: number | null;
  };
  by_provider: Record<string, { calls: number; input_tokens: number; output_tokens: number }>;
};

export type Dashboard = {
  generated_at: string;
  cached: boolean;
  sources: { fleet: string | null; incidents: string | null; assets: string | null };
  fleet: {
    device_count: number;
    scored_device_count: number;
    health: { bands: Record<string, number>; average_score: number | null; band_thresholds: string };
    operating_systems: Record<string, number>;
    compliance: Record<string, number>;
    patching: {
      devices_scanned: number;
      devices_missing_updates: number;
      devices_missing_critical: number;
      missing_update_count: number;
      missing_critical_count: number;
      top_missing_updates: {
        patch_id: string;
        title: string | null;
        severity: string | null;
        classification: string | null;
        devices_missing: number;
        failure_reasons: Record<string, number>;
      }[];
      failure_reasons: Record<string, number>;
    };
    devices_needing_attention: {
      device_id: string;
      hostname: string | null;
      os_name: string;
      health_score: number;
      band: string;
      compliance_state: string;
      missing_critical_patches: number;
      disk_used_pct: number;
      largest_penalty: { component: string; measurement: string } | null;
    }[];
  };
  incidents: {
    total: number;
    open: number;
    by_state: Record<string, number>;
    by_priority: Record<string, number>;
    by_category: Record<string, number>;
    by_channel: Record<string, number>;
    opened_recently: { days: number; count: number };
    ageing: { open_average_days: number | null; open_oldest_days: number | null };
    resolution_quality: {
      resolved: number;
      resolved_as_workaround: number;
      workaround_rate_pct: number | null;
      reopened: number;
      note: string;
      examples: { number: string; short_description: string; device_id: string | null; resolved_days_ago: number | null }[];
    };
    repeats: {
      devices_affected: number;
      incidents_on_repeat_devices: number;
      callers_affected: number;
      incidents_from_repeat_callers: number;
    };
    repeat_callers: {
      caller_id: string;
      display_name: string | null;
      department: string | null;
      vip: boolean;
      incidents: number;
      workarounds: number;
    }[];
    repeat_devices: { device_id: string; incidents: number; workarounds: number; categories: string[] }[];
  };
  assets: {
    total: number;
    by_lifecycle_state: Record<string, number>;
    by_category: Record<string, number>;
    warranty: { in_warranty: number; out_of_warranty: number; expiring_within_days: number; expiring_soon: number };
    refresh: {
      overdue: number;
      average_age_days: number | null;
      candidates: { asset_tag: string; device_id: string | null; model: string | null; days_overdue: number; warranty_active: boolean }[];
    };
    spares_by_subcategory: Record<string, number>;
    purchase_cost_total: number;
  };
  governance: {
    investigations: { total: number; by_state: Record<string, number>; funnel: { state: string; count: number }[] };
    approvals: { pending: number; approved: number; rejected: number };
    verification: { total: number; confirmed: number; not_confirmed: number; disagreements: number };
    policy: { refusals: number; governed_tool_calls: number };
  };
};

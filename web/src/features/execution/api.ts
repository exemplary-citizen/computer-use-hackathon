import type { RunState } from "../../contracts";

const ROOT = "/api/execution";

/** The three inputs declared by the approved bundle. */
export interface RunInputs {
  lead_name: string;
  lifecycle_status: string;
  owner_name: string;
}

export const TARGET_APPS = [
  { value: "crm_a", label: "CRM A — Northlight" },
  { value: "crm_b", label: "CRM B — Meridian" },
] as const;

export const RUN_INPUT_FIELDS = [
  { name: "lead_name", label: "Lead name", placeholder: "Ada Lovelace" },
  { name: "lifecycle_status", label: "Lifecycle status", placeholder: "Qualified" },
  { name: "owner_name", label: "Owner", placeholder: "Sam Chen" },
] as const;

/** Short label used in headlines and button copy. */
export function targetAppLabel(targetApp: string): string {
  return targetApp === "crm_b" ? "CRM B" : "CRM A";
}

/** Full label used in the run configuration form and previews. */
export function targetAppFullLabel(targetApp: string): string {
  return TARGET_APPS.find((app) => app.value === targetApp)?.label ?? targetAppLabel(targetApp);
}

export function inputLabel(name: string): string {
  return RUN_INPUT_FIELDS.find((field) => field.name === name)?.label ?? name.replaceAll("_", " ");
}

export interface ExecutionFaultPayload {
  error_code: string;
  message: string;
  cause?: string;
  remediation?: string;
  docs_anchor?: string;
  detail?: string;
}

export interface RunRequestInfo {
  id: string;
  automation_id: string;
  version: number;
  target_app: string;
  inputs: Record<string, unknown>;
  invocation_source: string;
  max_steps: number;
  max_time_seconds: number;
  created_at: string;
}

export interface RunPreview {
  request: RunRequestInfo;
  automation_name: string;
  normalized_inputs: Record<string, unknown>;
  missing_fields: string[];
  requires_confirmation: boolean;
}

export interface FieldChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface StagedChange {
  run_id: string;
  target_app: string;
  record_identity: string;
  changes: FieldChange[];
  visible_verification: string;
  session_reference: string;
  payload_sha256: string;
  created_at?: string;
}

export interface RunResult {
  run_id: string;
  state: RunState;
  answer: string | null;
  verification_summary: string | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string;
  completed_at: string;
  holo_steps: number;
}

export interface RunStatus {
  id: string;
  state: RunState;
  request: RunRequestInfo;
  staged_change: StagedChange | null;
  result: RunResult | null;
  last_sequence: number;
  approval_timeout_seconds: number;
}

/** Wire shape of one event from GET /events and the WS stream (snake_case). */
export interface ExecutionRunEvent {
  run_id: string;
  sequence: number;
  state: RunState;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RunSummary {
  id: string;
  state: RunState;
  request: RunRequestInfo;
  created_at: string;
  updated_at: string;
}

/** Backend fault payload (error_code/message/cause/remediation) as an Error. */
export class ExecutionFaultError extends Error {
  readonly fault: ExecutionFaultPayload;
  readonly status: number;

  constructor(fault: ExecutionFaultPayload, status: number) {
    super(fault.message);
    this.name = "ExecutionFaultError";
    this.fault = fault;
    this.status = status;
  }
}

/** 422 field_errors from run preparation, keyed by input name. */
export class InputFieldError extends Error {
  readonly fieldErrors: Record<string, string>;

  constructor(fieldErrors: Record<string, string>) {
    super("Some inputs need attention.");
    this.name = "InputFieldError";
    this.fieldErrors = fieldErrors;
  }
}

let tokenPromise: Promise<string> | null = null;

async function fetchToken(): Promise<string> {
  const response = await fetch(`${ROOT}/csrf-token`);
  if (!response.ok) throw await toError(response);
  const payload = (await response.json()) as { token: string };
  return payload.token;
}

/** Fetch the per-boot mutation token once and reuse it for every POST. */
function csrfToken(): Promise<string> {
  tokenPromise ??= fetchToken().catch((reason: unknown) => {
    tokenPromise = null;
    throw reason;
  });
  return tokenPromise;
}

/** Test hook: forget the cached token so mocked fetches stay isolated. */
export function resetExecutionTokenCache(): void {
  tokenPromise = null;
}

async function toError(response: Response): Promise<Error> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // Fall through to the generic HTTP error below.
  }
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (response.status === 422 && record.field_errors && typeof record.field_errors === "object") {
      return new InputFieldError(record.field_errors as Record<string, string>);
    }
    if (typeof record.error_code === "string" && typeof record.message === "string") {
      return new ExecutionFaultError(record as unknown as ExecutionFaultPayload, response.status);
    }
    if (typeof record.detail === "string") {
      return new ExecutionFaultError({ error_code: "http_error", message: record.detail }, response.status);
    }
  }
  return new ExecutionFaultError(
    { error_code: "http_error", message: `${response.status} ${response.statusText}` },
    response.status,
  );
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${ROOT}${path}`);
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const token = await csrfToken();
  const response = await fetch(`${ROOT}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Foundry-Token": token },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

/** WebSocket URL for the live event stream, resuming after `sinceSeq`. */
export function eventStreamUrl(runId: string, sinceSeq: number): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${ROOT}/runs/${runId}/events/stream?since_seq=${sinceSeq}`;
}

export const executionApi = {
  prepareRun: (targetApp: string, inputs: RunInputs) =>
    post<RunPreview>("/runs", { target_app: targetApp, inputs }),
  confirmStart: (runId: string) => post<{ status: string }>(`/runs/${runId}/confirm-start`),
  confirmCommit: (runId: string, payloadSha256: string, actor: string) =>
    post<{ status: string }>(`/runs/${runId}/confirm-commit`, { payload_sha256: payloadSha256, actor }),
  rejectCommit: (runId: string) => post<{ status: string }>(`/runs/${runId}/reject-commit`, {}),
  cancelRun: (runId: string, reason?: string) =>
    post<{ status: string }>(`/runs/${runId}/cancel`, reason ? { reason } : {}),
  getStatus: (runId: string) => get<RunStatus>(`/runs/${runId}`),
  getEvents: (runId: string, afterSeq: number) =>
    get<{ events: ExecutionRunEvent[] }>(`/runs/${runId}/events?after_seq=${afterSeq}`),
  listRuns: () => get<{ runs: RunSummary[] }>("/runs"),
};

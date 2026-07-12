import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetExecutionTokenCache, type RunStatus, type RunSummary } from "./api";
import { ExecutionPage } from "./ExecutionPage";

const RUN_ID = "3f1c9a52-6f0f-4a5e-9a3f-2f9d4a3b8c7d";

const request = {
  id: RUN_ID,
  automation_id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d",
  version: 1,
  target_app: "crm_a",
  inputs: { lead_name: "Ada Lovelace", lifecycle_status: "Qualified", owner_name: "Sam Chen" },
  invocation_source: "dashboard",
  max_steps: 40,
  max_time_seconds: 180,
  created_at: "2026-07-11T12:00:00Z",
};

const stagedChange = {
  run_id: RUN_ID,
  target_app: "crm_a",
  record_identity: "Lead: Ada Lovelace",
  changes: [
    { field: "lifecycle_status", before: "New", after: "Qualified" },
    { field: "owner_name", before: "Unassigned", after: "Sam Chen" },
  ],
  visible_verification: "The edit form shows Qualified and Sam Chen; Save has not been pressed.",
  session_reference: "session-1",
  payload_sha256: "a".repeat(64),
  created_at: "2026-07-11T12:01:00Z",
};

function statusOf(state: RunStatus["state"], extra: Partial<RunStatus> = {}): RunStatus {
  return {
    id: RUN_ID,
    state,
    request,
    staged_change: null,
    result: null,
    last_sequence: -1,
    approval_timeout_seconds: 120,
    ...extra,
  };
}

function summaryOf(state: RunSummary["state"]): RunSummary {
  return {
    id: RUN_ID,
    state,
    request,
    created_at: "2026-07-11T12:00:00Z",
    updated_at: "2026-07-11T12:01:00Z",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type RouteResult = Response | Promise<Response> | object | undefined;

function installFetch(router: (method: string, url: string, init?: RequestInit) => RouteResult) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const result = router(init?.method ?? "GET", url, init);
    if (result instanceof Response) return Promise.resolve(result);
    if (result instanceof Promise) return result;
    if (result !== undefined) return Promise.resolve(json(result));
    return Promise.reject(new Error(`Unhandled fetch: ${init?.method ?? "GET"} ${url}`));
  });
}

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static latest(): FakeWebSocket | undefined {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }

  url: string;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.readyState = 3;
  }

  /** Simulate the server or network dropping the connection. */
  drop(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

/** Routes shared by every test that lands on a live run surface. */
function baseRouter(current: () => RunStatus) {
  return (method: string, url: string): RouteResult => {
    if (url.endsWith("/csrf-token")) return { token: "test-token" };
    if (method === "GET" && url.endsWith("/api/execution/runs")) return { runs: [summaryOf(current().state)] };
    if (method === "GET" && url.includes(`/runs/${RUN_ID}/events?`)) return { events: [] };
    if (method === "GET" && url.endsWith(`/runs/${RUN_ID}`)) return current();
    return undefined;
  };
}

async function openApprovalSurface() {
  render(<ExecutionPage />);
  fireEvent.click(await screen.findByRole("button", { name: /Ada Lovelace/ }));
  await screen.findByRole("heading", { name: "Approve the staged change" });
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetExecutionTokenCache();
});

describe("ExecutionPage run configuration", () => {
  it("renders the approved bundle's create-contact inputs", async () => {
    installFetch((method, url) => {
      if (method === "GET" && url.endsWith("/api/execution/automation")) {
        return {
          name: "Create a CRM contact",
          version: 1,
          operation: "create",
          inputs: [
            {
              name: "first_name",
              json_type: "string",
              description: "Given name for the new contact.",
              required: true,
              default: null,
              examples: ["Amina"],
            },
            {
              name: "last_name",
              json_type: "string",
              description: "Family name for the new contact.",
              required: true,
              default: null,
              examples: ["Diallo"],
            },
          ],
          input_schema: { type: "object" },
        };
      }
      if (method === "GET" && url.endsWith("/api/execution/runs")) return { runs: [] };
      return undefined;
    });

    render(<ExecutionPage />);

    expect(await screen.findByLabelText(/First name/)).toHaveAttribute("placeholder", "Amina");
    expect(screen.getByLabelText(/Last name/)).toHaveAttribute("placeholder", "Diallo");
    expect(screen.queryByLabelText("Lead name")).not.toBeInTheDocument();
  });

  it("renders field-level errors from a 422 and an empty-state that points at the form", async () => {
    installFetch((method, url) => {
      if (url.endsWith("/csrf-token")) return { token: "test-token" };
      if (method === "GET" && url.endsWith("/api/execution/runs")) return { runs: [] };
      if (method === "POST" && url.endsWith("/api/execution/runs")) {
        return json(
          { field_errors: { lead_name: "Lead name is required.", owner_name: "Owner is required." } },
          422,
        );
      }
      return undefined;
    });

    render(<ExecutionPage />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
    expect(screen.getByText("Use the form above to configure and prepare the first run.")).toBeInTheDocument();
    expect(screen.getByLabelText("Target app")).toBeInTheDocument();
    expect(screen.getByLabelText("Target app")).toHaveValue("crm_b");
    expect(screen.getByText("CRM A — Northlight")).toBeInTheDocument();
    expect(screen.getByText("CRM B — Meridian")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Prepare run" }));

    expect(await screen.findByText("Lead name is required.")).toBeInTheDocument();
    expect(screen.getByText("Owner is required.")).toBeInTheDocument();
  });

  it("shows the interpreted-command preview with a Confirm start action after prepare", async () => {
    installFetch((method, url) => {
      if (url.endsWith("/csrf-token")) return { token: "test-token" };
      if (method === "GET" && url.endsWith("/api/execution/runs")) return { runs: [] };
      if (method === "POST" && url.endsWith("/api/execution/runs")) {
        return json(
          {
            request,
            automation_name: "Update CRM lead",
            normalized_inputs: request.inputs,
            missing_fields: [],
            requires_confirmation: true,
          },
          201,
        );
      }
      if (method === "GET" && url.endsWith(`/runs/${RUN_ID}`)) return statusOf("awaiting_start_confirmation");
      if (method === "GET" && url.includes("/events?")) return { events: [] };
      return undefined;
    });

    render(<ExecutionPage />);
    await screen.findByText("No runs yet");
    fireEvent.change(screen.getByLabelText("Lead name"), { target: { value: "Ada Lovelace" } });
    fireEvent.change(screen.getByLabelText("Lifecycle status"), { target: { value: "Qualified" } });
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "Sam Chen" } });
    fireEvent.click(screen.getByRole("button", { name: "Prepare run" }));

    expect(await screen.findByRole("heading", { name: "Confirm the interpreted command" })).toBeInTheDocument();
    expect(screen.getByText("Update CRM lead")).toBeInTheDocument();
    expect(screen.getByText("CRM A — Northlight")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm start" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
  });
});

describe("ExecutionPage executing surface", () => {
  it("curates heartbeats into a single subtle line and shows the kill-switch hint", async () => {
    const current = statusOf("executing", { last_sequence: 3 });
    installFetch((method, url) => {
      if (url.endsWith("/csrf-token")) return { token: "test-token" };
      if (method === "GET" && url.endsWith("/api/execution/runs")) return { runs: [summaryOf("executing")] };
      if (method === "GET" && url.includes(`/runs/${RUN_ID}/events?`)) {
        return {
          events: [
            {
              run_id: RUN_ID,
              sequence: 0,
              state: "executing",
              event_type: "session_started",
              message: "Holo session started.",
              payload: {},
              created_at: "2026-07-11T12:00:01Z",
            },
            {
              run_id: RUN_ID,
              sequence: 1,
              state: "executing",
              event_type: "heartbeat",
              message: "Still working; the session is active.",
              payload: {},
              created_at: "2026-07-11T12:00:11Z",
            },
            {
              run_id: RUN_ID,
              sequence: 2,
              state: "executing",
              event_type: "heartbeat",
              message: "Still working; the session is active.",
              payload: {},
              created_at: "2026-07-11T12:00:21Z",
            },
            {
              run_id: RUN_ID,
              sequence: 3,
              state: "executing",
              event_type: "heartbeat",
              message: "Still working; the session is active.",
              payload: {},
              created_at: "2026-07-11T12:00:31Z",
            },
          ],
        };
      }
      if (method === "GET" && url.endsWith(`/runs/${RUN_ID}`)) return current;
      return undefined;
    });

    render(<ExecutionPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Ada Lovelace/ }));

    expect(await screen.findByText("Holo session started.")).toBeInTheDocument();
    expect(screen.getAllByText("Still working…")).toHaveLength(1);
    expect(screen.getByText("Kill switch: press Esc twice on the desktop.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
  });
});

describe("ExecutionPage approval surface", () => {
  it("renders the staged diff, verification quote, countdown, and count-labelled approve button", async () => {
    const current = statusOf("awaiting_commit_approval", { staged_change: stagedChange });
    installFetch(baseRouter(() => current));

    await openApprovalSurface();

    const approve = await screen.findByRole("button", { name: "Approve 2 changes to CRM A" });
    expect(approve).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(screen.getByText("Lead: Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("lifecycle_status")).toBeInTheDocument();
    expect(screen.getByText("Qualified")).toBeInTheDocument();
    expect(screen.getByText(/Save has not been pressed/)).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent(/Approval window: \d+s remaining/);
  });

  it("keeps Approve disabled until the staged change has rendered", async () => {
    const current = statusOf("awaiting_commit_approval", { staged_change: null });
    installFetch(baseRouter(() => current));

    await openApprovalSurface();

    expect(screen.getByText("Loading the staged change…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("disables both decision controls after the first click", async () => {
    const current = statusOf("awaiting_commit_approval", { staged_change: stagedChange });
    installFetch((method, url) => {
      if (method === "POST" && url.endsWith("/confirm-commit")) return new Promise<Response>(() => undefined);
      return baseRouter(() => current)(method, url);
    });

    await openApprovalSurface();
    const approve = await screen.findByRole("button", { name: "Approve 2 changes to CRM A" });
    fireEvent.click(approve);

    expect(approve).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("shows 'No changes were saved.' after rejecting the staged change", async () => {
    let current = statusOf("awaiting_commit_approval", { staged_change: stagedChange });
    installFetch((method, url) => {
      if (method === "POST" && url.endsWith("/reject-commit")) {
        current = statusOf("cancelled", {
          staged_change: stagedChange,
          result: {
            run_id: RUN_ID,
            state: "cancelled",
            answer: "Staged change rejected; nothing was saved.",
            verification_summary: null,
            error_code: null,
            error_message: null,
            started_at: "2026-07-11T12:00:00Z",
            completed_at: "2026-07-11T12:02:00Z",
            holo_steps: 4,
          },
        });
        return { status: "rejected" };
      }
      return baseRouter(() => current)(method, url);
    });

    await openApprovalSurface();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));

    expect(await screen.findByText("No changes were saved.")).toBeInTheDocument();
    expect(screen.getByText("Staged change rejected; nothing was saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start another run" })).toBeEnabled();
  });

  it("surfaces the 409 message when approval arrives after the window expired", async () => {
    let current = statusOf("awaiting_commit_approval", { staged_change: stagedChange });
    installFetch((method, url) => {
      if (method === "POST" && url.endsWith("/confirm-commit")) {
        current = statusOf("cancelled", {
          staged_change: stagedChange,
          result: {
            run_id: RUN_ID,
            state: "cancelled",
            answer: "Approval window expired; nothing was saved.",
            verification_summary: null,
            error_code: null,
            error_message: null,
            started_at: "2026-07-11T12:00:00Z",
            completed_at: "2026-07-11T12:03:00Z",
            holo_steps: 4,
          },
        });
        return json(
          {
            error_code: "stale_approval",
            message: "The approval arrived after the approval window expired.",
            cause: "The run was already cancelled by the approval timeout.",
            remediation: "Start a new run and approve within the countdown shown on the approval screen.",
          },
          409,
        );
      }
      return baseRouter(() => current)(method, url);
    });

    await openApprovalSurface();
    fireEvent.click(await screen.findByRole("button", { name: "Approve 2 changes to CRM A" }));

    expect(
      await screen.findByText("The approval arrived after the approval window expired."),
    ).toBeInTheDocument();
    expect(await screen.findByText("No changes were saved.")).toBeInTheDocument();
  });

  it("disables approval controls and shows Reconnecting… when the event stream drops", async () => {
    const current = statusOf("awaiting_commit_approval", { staged_change: stagedChange });
    installFetch(baseRouter(() => current));

    await openApprovalSurface();
    const approve = await screen.findByRole("button", { name: "Approve 2 changes to CRM A" });
    expect(approve).toBeEnabled();

    const socket = FakeWebSocket.latest();
    expect(socket).toBeDefined();
    act(() => {
      socket?.drop();
    });

    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
    expect(approve).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});

describe("ExecutionPage result surface", () => {
  it("shows the zero-shot headline for a succeeded crm_b run", async () => {
    const crmBRequest = { ...request, target_app: "crm_b" };
    const current = statusOf("succeeded", {
      request: crmBRequest,
      staged_change: { ...stagedChange, target_app: "crm_b" },
      result: {
        run_id: RUN_ID,
        state: "succeeded",
        answer: "Committed and verified.",
        verification_summary: "Persisted state matches the approved staged change exactly.",
        error_code: null,
        error_message: null,
        started_at: "2026-07-11T12:00:00Z",
        completed_at: "2026-07-11T12:02:30Z",
        holo_steps: 12,
      },
    });
    installFetch(baseRouter(() => current));

    render(<ExecutionPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Ada Lovelace/ }));

    expect(
      await screen.findByRole("heading", { name: "Zero-shot: CRM B — exactly these fields changed" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Persisted state matches the approved staged change exactly.")).toBeInTheDocument();
    expect(screen.getByText(/Duration: 150\.0s/)).toBeInTheDocument();
    expect(screen.getByText("Qualified")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start another run" })).toBeEnabled();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import { executionApi, resetExecutionTokenCache } from "./api";

const INPUTS = { lead_name: "Ada", lifecycle_status: "Qualified", owner_name: "Sam" };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  resetExecutionTokenCache();
});

describe("execution api client", () => {
  it("fetches the CSRF token once and sends it on every mutating call", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/csrf-token")) return json({ token: "boot-token" });
      return json({ status: "ok" });
    });

    await executionApi.confirmStart("run-1");
    await executionApi.rejectCommit("run-1");

    const tokenCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/csrf-token"));
    expect(tokenCalls).toHaveLength(1);

    const startCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/confirm-start"));
    expect(startCall).toBeDefined();
    expect(new Headers(startCall?.[1]?.headers).get("X-Foundry-Token")).toBe("boot-token");
  });

  it("raises field errors for 422 run-preparation responses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/csrf-token")) return json({ token: "boot-token" });
      return json({ field_errors: { lead_name: "Lead name is required." } }, 422);
    });

    await expect(executionApi.prepareRun("crm_a", INPUTS)).rejects.toMatchObject({
      name: "InputFieldError",
      fieldErrors: { lead_name: "Lead name is required." },
    });
  });

  it("raises typed faults carrying error_code, cause, and remediation", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/csrf-token")) return json({ token: "boot-token" });
      return json(
        {
          error_code: "stale_approval",
          message: "The approval arrived after the approval window expired.",
          cause: "The run was already cancelled by the approval timeout.",
          remediation: "Start a new run and approve within the countdown shown on the approval screen.",
        },
        409,
      );
    });

    await expect(executionApi.confirmCommit("run-1", "a".repeat(64), "local-user")).rejects.toMatchObject({
      name: "ExecutionFaultError",
      status: 409,
      message: "The approval arrived after the approval window expired.",
      fault: { error_code: "stale_approval" },
    });
  });

  it("retries the token fetch after a failure instead of caching the rejection", async () => {
    let attempts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/csrf-token")) {
        attempts += 1;
        if (attempts === 1) return json({ error_code: "forbidden_origin", message: "Rejected." }, 403);
        return json({ token: "boot-token" });
      }
      return json({ status: "ok" });
    });

    await expect(executionApi.confirmStart("run-1")).rejects.toMatchObject({ status: 403 });
    await expect(executionApi.confirmStart("run-1")).resolves.toEqual({ status: "ok" });
    expect(attempts).toBe(2);
  });
});

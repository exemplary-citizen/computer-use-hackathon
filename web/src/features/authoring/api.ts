import type {
  AutomationDetail,
  AutomationManifest,
  AutomationVersion,
  ValidationResponse,
} from "../../contracts";

const ROOT = "/api/authoring";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ROOT}${path}`, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // The status text remains a safe fallback for non-JSON failures.
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const authoringApi = {
  list: () => request<AutomationManifest[]>("/automations"),
  detail: (id: string) => request<AutomationDetail>(`/automations/${id}`),
  create: (form: FormData) =>
    request<AutomationManifest>("/automations", { method: "POST", body: form }),
  editArtifact: (id: string, version: number, artifact: string, content: string) =>
    request<AutomationVersion>(
      `/automations/${id}/versions/${version}/artifacts/${encodeURIComponent(artifact)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      },
    ),
  validate: (id: string, version: number) =>
    request<ValidationResponse>(`/automations/${id}/versions/${version}/validate`, {
      method: "POST",
    }),
  runToolTests: (id: string, version: number) =>
    request<{ passed: boolean; tests_run: number; failures: string[] }>(
      `/automations/${id}/versions/${version}/tool-tests`,
      { method: "POST" },
    ),
  approve: (id: string, version: number, actor: string) =>
    request<AutomationManifest>(`/automations/${id}/versions/${version}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor }),
    }),
  regenerate: (id: string) =>
    request<{ status: string }>(`/automations/${id}/regenerate`, { method: "POST" }),
  deactivate: (id: string) =>
    request<AutomationManifest>(`/automations/${id}/deactivate`, { method: "POST" }),
  delete: (id: string) => request<void>(`/automations/${id}`, { method: "DELETE" }),
};

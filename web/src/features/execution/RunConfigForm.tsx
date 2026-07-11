import type { FormEvent } from "react";

import { RUN_INPUT_FIELDS, TARGET_APPS, type RunInputs } from "./api";

/**
 * Run configuration surface: target app plus the bundle's three inputs.
 * Field-level 422 errors render under the matching input.
 */
export function RunConfigForm({
  busy,
  fieldErrors,
  onSubmit,
}: {
  busy: boolean;
  fieldErrors: Record<string, string>;
  onSubmit: (targetApp: string, inputs: RunInputs) => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    onSubmit(String(data.get("target_app") ?? "crm_a"), {
      lead_name: String(data.get("lead_name") ?? "").trim(),
      lifecycle_status: String(data.get("lifecycle_status") ?? "").trim(),
      owner_name: String(data.get("owner_name") ?? "").trim(),
    });
  }

  const knownFields = new Set<string>(["target_app", ...RUN_INPUT_FIELDS.map((field) => field.name)]);
  const otherErrors = Object.entries(fieldErrors).filter(([name]) => !knownFields.has(name));

  return (
    <form className="create-form" onSubmit={submit}>
      <h3>Configure a run</h3>
      <label>
        Target app
        <select defaultValue={TARGET_APPS[0].value} name="target_app">
          {TARGET_APPS.map((app) => (
            <option key={app.value} value={app.value}>
              {app.label}
            </option>
          ))}
        </select>
        {fieldErrors.target_app ? (
          <small className="field-error" role="alert">
            {fieldErrors.target_app}
          </small>
        ) : null}
      </label>
      {RUN_INPUT_FIELDS.map((field) => (
        <label key={field.name}>
          {field.label}
          <input name={field.name} placeholder={field.placeholder} />
          {fieldErrors[field.name] ? (
            <small className="field-error" role="alert">
              {fieldErrors[field.name]}
            </small>
          ) : null}
        </label>
      ))}
      {otherErrors.length ? (
        <div className="callout danger" role="alert">
          {otherErrors.map(([name, message]) => (
            <p key={name}>{message}</p>
          ))}
        </div>
      ) : null}
      <button disabled={busy} type="submit">
        {busy ? "Preparing…" : "Prepare run"}
      </button>
    </form>
  );
}

import { useEffect, useState, type FormEvent } from "react";

import {
  DEFAULT_TARGET_APP,
  RUN_INPUT_FIELDS,
  TARGET_APPS,
  executionApi,
  inputLabel,
  type RunInputs,
  type RuntimeInputDefinition,
} from "./api";

/**
 * Run configuration surface: target app plus the approved bundle's inputs.
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
  const [inputFields, setInputFields] = useState<RuntimeInputDefinition[]>(
    RUN_INPUT_FIELDS.map((field) => ({
      ...field,
      json_type: "string",
      description: "",
      default: null,
      examples: [field.placeholder],
    })),
  );

  useEffect(() => {
    let active = true;
    executionApi
      .getAutomation()
      .then((automation) => {
        if (active && automation.inputs.length) setInputFields(automation.inputs);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const inputs: RunInputs = {};
    for (const field of inputFields) {
      const value = String(data.get(field.name) ?? "").trim();
      if (field.required || value) inputs[field.name] = value;
    }
    onSubmit(String(data.get("target_app") ?? DEFAULT_TARGET_APP), inputs);
  }

  const knownFields = new Set<string>(["target_app", ...inputFields.map((field) => field.name)]);
  const otherErrors = Object.entries(fieldErrors).filter(([name]) => !knownFields.has(name));

  return (
    <form className="create-form" onSubmit={submit}>
      <h3>Configure a run</h3>
      <label>
        Target app
        <select defaultValue={DEFAULT_TARGET_APP} name="target_app">
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
      {inputFields.map((field) => (
        <label key={field.name}>
          {inputLabel(field.name)}
          <input
            defaultValue={typeof field.default === "string" ? field.default : ""}
            name={field.name}
            placeholder={typeof field.examples[0] === "string" ? field.examples[0] : undefined}
            aria-required={field.required}
          />
          {field.description ? <small>{field.description}</small> : null}
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

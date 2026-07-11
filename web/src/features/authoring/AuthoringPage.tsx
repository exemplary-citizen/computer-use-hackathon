import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import type { AutomationDetail, AutomationManifest } from "../../contracts";
import { authoringApi } from "./api";

export function AuthoringPage() {
  const [automations, setAutomations] = useState<AutomationManifest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setAutomations(await authoringApi.list());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load automations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void authoringApi
      .list()
      .then((items) => {
        if (active) setAutomations(items);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Unable to load automations");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (selectedId) {
    return (
      <AutomationReview
        automationId={selectedId}
        onBack={() => {
          setSelectedId(null);
          void refresh();
        }}
      />
    );
  }

  return (
    <section aria-labelledby="authoring-title" className="surface-card authoring-page">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Member 1</p>
          <h2 id="authoring-title">Teach and review automations</h2>
          <p>Turn a demonstration, an SOP, or both into a versioned bundle that is safe to run.</p>
        </div>
        <button type="button" onClick={() => setCreating((value) => !value)}>
          {creating ? "Close form" : "New automation"}
        </button>
      </div>

      {creating ? (
        <CreateAutomation
          onCreated={(automation) => {
            setCreating(false);
            setAutomations((current) => [automation, ...current]);
            setSelectedId(automation.id);
          }}
        />
      ) : null}

      {error ? <div className="callout danger">{error}</div> : null}
      {loading ? <p role="status">Loading automations…</p> : null}
      {!loading && automations.length === 0 ? (
        <div className="empty-state">
          <h3>No automations yet</h3>
          <p>Add a supported video or SOP to begin the evidence-to-skill workflow.</p>
        </div>
      ) : null}
      <div className="automation-list">
        {automations.map((automation) => (
          <button
            className="automation-row"
            key={automation.id}
            onClick={() => setSelectedId(automation.id)}
            type="button"
          >
            <span>
              <strong>{automation.name}</strong>
              <small>{automation.sources.map((source) => source.original_name).join(" · ") || "No sources"}</small>
            </span>
            <span className={`status status-${automation.status}`}>{formatStatus(automation.status)}</span>
            <span>v{automation.approved_version ?? automation.current_version ?? "—"}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function CreateAutomation({ onCreated }: { onCreated: (automation: AutomationManifest) => void }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    setSubmitting(true);
    try {
      const automation = await authoringApi.create(formData);
      onCreated(automation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="create-form" onSubmit={(event) => void submit(event)}>
      <h3>New automation</h3>
      <label>
        Task name
        <input name="name" required maxLength={120} placeholder="Update a CRM lead" />
      </label>
      <label>
        Video and/or SOP
        <input
          name="sources"
          required
          multiple
          type="file"
          accept="video/mp4,video/quicktime,video/webm,application/pdf,text/markdown,text/plain"
        />
        <small>Up to one 10-minute video and one PDF, Markdown, or text SOP.</small>
      </label>
      <label className="disclosure">
        <input name="provider_disclosure_accepted" type="checkbox" value="true" required />
        <span>
          I understand selected frames and transcript/SOP text are staged for hosted Holo3, and audio is sent
          to Gradium when transcription is configured.
        </span>
      </label>
      {error ? <div className="callout danger">{error}</div> : null}
      <button disabled={submitting} type="submit">
        {submitting ? "Uploading…" : "Create and process"}
      </button>
    </form>
  );
}

function AutomationReview({ automationId, onBack }: { automationId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<AutomationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDetail(await authoringApi.detail(automationId));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load automation");
    }
  }, [automationId]);

  useEffect(() => {
    let active = true;
    void authoringApi
      .detail(automationId)
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Unable to load automation");
      });
    return () => {
      active = false;
    };
  }, [automationId]);

  useEffect(() => {
    if (detail?.manifest.status !== "processing") return undefined;
    const timer = window.setInterval(() => void load(), 2_000);
    return () => window.clearInterval(timer);
  }, [detail?.manifest.status, load]);

  async function action(callback: () => Promise<unknown>) {
    setBusy(true);
    try {
      await callback();
      await load();
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return (
      <section className="surface-card">
        <button className="secondary" type="button" onClick={onBack}>← Automations</button>
        <p role="status">{error ?? "Loading automation…"}</p>
      </section>
    );
  }

  const version = detail.version;
  return (
    <section aria-labelledby="review-title" className="surface-card review-page">
      <button className="secondary" type="button" onClick={onBack}>← Automations</button>
      <div className="section-heading review-heading">
        <div>
          <p className="eyebrow">{detail.manifest.slug}</p>
          <h2 id="review-title">{detail.manifest.name}</h2>
          <p>
            Current version {detail.manifest.current_version ?? "—"} · Approved version{" "}
            {detail.manifest.approved_version ?? "none"}
          </p>
        </div>
        <span className={`status status-${detail.manifest.status}`}>{formatStatus(detail.manifest.status)}</span>
      </div>

      {detail.manifest.status === "processing" ? (
        <div className="callout" role="status">
          <strong>{detail.progress?.message ?? "Evidence is being processed"}</strong>
          <progress max={100} value={detail.progress?.percent ?? 0} />
          <small>{detail.progress?.stage ?? "queued"} · This page refreshes automatically.</small>
        </div>
      ) : null}
      {detail.failure ? <div className="callout danger">{detail.failure}</div> : null}
      {error ? <div className="callout danger">{error}</div> : null}

      {version ? (
        <>
          <ValidationPanel detail={detail} />
          <ConflictPanel detail={detail} />
          <ArtifactEditor
            key={`${detail.manifest.current_version}-${detail.manifest.updated_at}`}
            detail={detail}
            disabled={busy}
            onSaved={load}
          />
          <div className="action-bar">
            <button disabled={busy} type="button" onClick={() => void action(() => authoringApi.validate(automationId, version.version))}>
              Validate
            </button>
            {version.tools.length ? (
              <button
                className="secondary"
                disabled={busy}
                type="button"
                onClick={() => void action(() => authoringApi.runToolTests(automationId, version.version))}
              >
                Run tool tests
              </button>
            ) : null}
            <ApprovalAction detail={detail} disabled={busy} action={action} />
            <button className="secondary" disabled={busy} type="button" onClick={() => void action(() => authoringApi.regenerate(automationId))}>
              Regenerate
            </button>
          </div>
        </>
      ) : null}

      <div className="danger-zone">
        <button className="secondary" disabled={busy} type="button" onClick={() => void action(() => authoringApi.deactivate(automationId))}>
          Deactivate
        </button>
        <button
          className="danger-button"
          disabled={busy}
          type="button"
          onClick={() => {
            if (window.confirm("Delete this automation and all local artifacts?")) {
              void action(async () => {
                await authoringApi.delete(automationId);
                onBack();
              });
            }
          }}
        >
          Delete
        </button>
      </div>
    </section>
  );
}

function ValidationPanel({ detail }: { detail: AutomationDetail }) {
  if (!detail.validation) return null;
  return (
    <div className={`validation-summary ${detail.validation.valid ? "valid" : "invalid"}`}>
      <strong>{detail.validation.valid ? "Validation passed" : "Approval blocked"}</strong>
      {detail.validation.errors.length ? (
        <ul>
          {detail.validation.errors.map((issue) => (
            <li key={`${issue.code}-${issue.artifact ?? "bundle"}`}>{issue.message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ConflictPanel({ detail }: { detail: AutomationDetail }) {
  const conflicts = detail.version?.conflicts ?? [];
  if (!conflicts.length) return null;
  return (
    <section className="conflicts" aria-labelledby="conflicts-title">
      <h3 id="conflicts-title">Source conflicts</h3>
      {conflicts.map((conflict) => (
        <article key={conflict.id}>
          <span className={`status status-${conflict.severity}`}>{conflict.severity}</span>
          <p>{conflict.description}</p>
          <small>{conflict.resolution ?? "Unresolved"}</small>
        </article>
      ))}
    </section>
  );
}

function ArtifactEditor({
  detail,
  disabled,
  onSaved,
}: {
  detail: AutomationDetail;
  disabled: boolean;
  onSaved: () => Promise<void>;
}) {
  const names = useMemo(() => Object.keys(detail.artifacts).sort(), [detail.artifacts]);
  const [selected, setSelected] = useState(names[0] ?? "");
  const [content, setContent] = useState(detail.artifacts[selected] ?? "");
  const [message, setMessage] = useState<string | null>(null);

  if (!detail.version || !selected) return null;
  return (
    <section className="artifact-editor" aria-label="Artifact editor">
      <div className="artifact-tabs" role="tablist" aria-label="Bundle artifacts">
        {names.map((name) => (
          <button
            aria-selected={selected === name}
            className={selected === name ? "active" : ""}
            key={name}
            role="tab"
            type="button"
            onClick={() => {
              setSelected(name);
              setContent(detail.artifacts[name] ?? "");
              setMessage(null);
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <label htmlFor="artifact-content">Edit {selected}</label>
      <textarea id="artifact-content" value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} />
      <div className="editor-footer">
        <small>Saving an approved artifact creates a new unapproved version.</small>
        <button
          disabled={disabled}
          type="button"
          onClick={() => {
            if (!detail.version) return;
            void authoringApi
              .editArtifact(detail.manifest.id, detail.version.version, selected, content)
              .then(onSaved)
              .then(() => setMessage("Saved and revalidated"))
              .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "Save failed"));
          }}
        >
          Save artifact
        </button>
      </div>
      {message ? <p role="status">{message}</p> : null}
    </section>
  );
}

function ApprovalAction({
  detail,
  disabled,
  action,
}: {
  detail: AutomationDetail;
  disabled: boolean;
  action: (callback: () => Promise<unknown>) => Promise<void>;
}) {
  const [actor, setActor] = useState("");
  if (!detail.version) return null;
  return (
    <label className="approval-action">
      <span className="sr-only">Reviewer name</span>
      <input value={actor} onChange={(event) => setActor(event.target.value)} placeholder="Reviewer name" />
      <button
        disabled={disabled || !actor.trim() || !detail.validation?.valid}
        type="button"
        onClick={() => void action(() => authoringApi.approve(detail.manifest.id, detail.version!.version, actor))}
      >
        Approve version
      </button>
    </label>
  );
}

function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

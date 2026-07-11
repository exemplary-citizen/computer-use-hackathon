export type AutomationStatus =
  | "draft"
  | "processing"
  | "review_required"
  | "approved"
  | "inactive"
  | "failed";

export type RunState =
  | "prepared"
  | "awaiting_start_confirmation"
  | "executing"
  | "awaiting_commit_approval"
  | "committing"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface SourceMetadata {
  id: string;
  source_type: "video" | "sop";
  original_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

export interface AutomationManifest {
  id: string;
  slug: string;
  name: string;
  status: AutomationStatus;
  sources: SourceMetadata[];
  current_version: number | null;
  approved_version: number | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewConflict {
  id: string;
  description: string;
  severity: "info" | "warning" | "blocking";
  resolution: string | null;
}

export interface AutomationVersion {
  version: number;
  conflicts: ReviewConflict[];
  tools: Array<{ name: string; description: string }>;
  validation_passed: boolean;
  approval: { actor: string; created_at: string } | null;
}

export interface ValidationIssue {
  code: string;
  message: string;
  artifact: string | null;
}

export interface ValidationResponse {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface AutomationDetail {
  manifest: AutomationManifest;
  version: AutomationVersion | null;
  artifacts: Record<string, string>;
  validation: ValidationResponse | null;
  failure: string | null;
  progress: {
    stage: string;
    percent: number;
    message: string;
    updated_at: string;
  } | null;
}

export interface RunEvent {
  runId: string;
  sequence: number;
  state: RunState;
  eventType: string;
  message: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

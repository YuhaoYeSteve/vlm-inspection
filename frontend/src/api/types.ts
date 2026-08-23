export type TaskStatus =
  | "draft"
  | "queued"
  | "running"
  | "paused"
  | "review_required"
  | "exporting"
  | "completed"
  | "canceled";

export type ImageStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";
export type Verdict = "pass" | "fail" | "unverifiable";
export type FindingStatus = "pass" | "fail" | "not_visible";

export interface ApiErrorEnvelope {
  code: string;
  message: string;
  details?: unknown;
}

export interface ArkSettings {
  base_url: string;
  protocol: "responses" | "chat";
  model: string;
  concurrency: number;
  timeout_seconds: number;
  max_retries: number;
  thinking: "disabled" | "auto" | "enabled";
  api_key_configured: boolean;
}

export interface FeishuConnection {
  configured: boolean;
  connected: boolean;
  user_name: string | null;
  app_name: string | null;
  identity: string | null;
  token_status: string | null;
  expires_at: string | null;
  destination_path: string;
  reference_url: string;
  error_code: string | null;
}

export interface TemplateRule {
  rule_id: string;
  name: string;
  text: string;
}

export interface TemplateModelParams {
  temperature: number;
  max_output_tokens: number;
}

export interface TemplateSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  enabled: boolean;
  version: number;
  created_at: string;
}

export interface TemplateDetail extends TemplateSummary {
  version_id: string;
  system_prompt: string;
  task_prompt: string;
  rules: TemplateRule[];
  model_params: TemplateModelParams;
}

export interface Finding {
  rule_id: string;
  rule_name: string;
  status: FindingStatus;
  observation: string;
  visual_evidence: string;
}

export interface InspectionResult {
  schema_version: number;
  image_valid: boolean;
  unverifiable_reason: string | null;
  verdict: Verdict;
  summary: string;
  findings: Finding[];
}

export interface TaskCounts {
  pass: number;
  fail: number;
  unverifiable: number;
  failed: number;
}

export interface TaskImage {
  id: string;
  position: number;
  original_name: string;
  sha256: string;
  status: ImageStatus;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  original_url: string;
  thumbnail_url: string;
  result: InspectionResult | null;
}

export interface TaskSummary {
  id: string;
  name: string;
  status: TaskStatus;
  template_name: string;
  template_version: number;
  model: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  counts: TaskCounts;
  report_id: string | null;
  report_status: string | null;
  report_url: string | null;
}

export interface TaskDetail extends TaskSummary {
  template_snapshot: Record<string, unknown>;
  model_snapshot: Record<string, unknown>;
  images: TaskImage[];
}

export interface UploadIssue {
  filename: string;
  reason: string;
}

export interface UploadSummary {
  accepted: Array<{ id: string; filename: string; sha256: string }>;
  skipped: UploadIssue[];
  rejected: UploadIssue[];
}

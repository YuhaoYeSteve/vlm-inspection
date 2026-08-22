import type {
  ArkSettings,
  FeishuSettings,
  InspectionResult,
  TaskDetail,
  TaskSummary,
  TemplateDetail,
  TemplateModelParams,
  TemplateRule,
  TemplateSummary,
  UploadSummary,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly details?: unknown,
  ) {
    super(message);
  }
}

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

export class ApiClient {
  constructor(private readonly baseUrl = "") {}

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers);
    let body: BodyInit | undefined;
    if (options.body instanceof FormData) {
      body = options.body;
    } else if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.body);
    }
    const relativeUrl = `${this.baseUrl}${path}`;
    const url = this.baseUrl ? relativeUrl : new URL(relativeUrl, window.location.origin).toString();
    const response = await fetch(url, { ...options, headers, body });
    if (!response.ok) {
      const fallback = { code: "request_failed", message: `请求失败 (${response.status})` };
      const error = (await response.json().catch(() => fallback)) as Partial<typeof fallback> & {
        details?: unknown;
      };
      throw new ApiError(
        error.code ?? fallback.code,
        error.message ?? fallback.message,
        response.status,
        error.details,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  getArkSettings = () => this.request<ArkSettings>("/api/settings/ark");
  saveArkSettings = (body: Omit<ArkSettings, "api_key_configured"> & { api_key: string | null }) =>
    this.request<ArkSettings>("/api/settings/ark", { method: "PUT", body });
  testArk = () =>
    this.request<{ ok: boolean; protocol: string; model: string; latency_ms: number }>(
      "/api/settings/ark/test",
      { method: "POST" },
    );
  getFeishuSettings = () => this.request<FeishuSettings>("/api/settings/feishu");
  saveFeishuSettings = (
    body: Omit<FeishuSettings, "callback_url" | "app_secret_configured"> & {
      app_secret: string | null;
    },
  ) => this.request<FeishuSettings>("/api/settings/feishu", { method: "PUT", body });
  getFeishuStatus = () =>
    this.request<{ configured: boolean; connected: boolean; expires_at: number | null }>(
      "/api/auth/feishu/status",
    );
  startFeishuAuth = () =>
    this.request<{ authorization_url: string }>("/api/auth/feishu/start");
  disconnectFeishu = () =>
    this.request<void>("/api/auth/feishu/disconnect", { method: "DELETE" });

  listTemplates = () => this.request<TemplateSummary[]>("/api/templates");
  getTemplate = (id: string) => this.request<TemplateDetail>(`/api/templates/${id}`);
  updateTemplate = (
    id: string,
    body: {
      name: string;
      description: string;
      task_prompt: string;
      rules: TemplateRule[];
      model_params: TemplateModelParams;
    },
  ) => this.request<TemplateDetail>(`/api/templates/${id}`, { method: "PUT", body });
  testTemplate = (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return this.request<InspectionResult>(`/api/templates/${id}/test`, { method: "POST", body });
  };

  listTasks = () => this.request<TaskSummary[]>("/api/tasks");
  getTask = (id: string) => this.request<TaskDetail>(`/api/tasks/${id}`);
  createTask = (name: string, templateId: string) =>
    this.request<TaskDetail>("/api/tasks", {
      method: "POST",
      body: { name, template_id: templateId },
    });
  uploadTaskImages = (id: string, files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return this.request<UploadSummary>(`/api/tasks/${id}/images`, { method: "POST", body });
  };
  taskAction = (id: string, action: "start" | "pause" | "resume" | "cancel") =>
    this.request<{ task_id: string; status: "accepted" }>(`/api/tasks/${id}/${action}`, {
      method: "POST",
    });
  retryImage = (taskId: string, imageId: string) =>
    this.request<{ task_id: string; status: "accepted" }>(
      `/api/tasks/${taskId}/images/${imageId}/retry`,
      { method: "POST" },
    );
  generateReport = (id: string, includeFailures: boolean) =>
    this.request<{ export_id: string; status: "accepted" }>(`/api/tasks/${id}/report`, {
      method: "POST",
      body: { include_failures: includeFailures },
    });
  generateNewReport = (id: string) =>
    this.request<{ export_id: string; status: "accepted" }>(
      `/api/tasks/${id}/report/new-version`,
      { method: "POST" },
    );
}

export const api = new ApiClient();

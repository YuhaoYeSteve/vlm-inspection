import {
  ArrowClockwise,
  ArrowSquareOut,
  CloudArrowUp,
  Key,
  LinkSimple,
  LockKey,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { ArkSettings } from "../../api/types";

type ArkSettingsForm = Omit<ArkSettings, "api_key_configured">;

interface NumericDrafts {
  concurrency: string;
  timeout_seconds: string;
  max_retries: string;
}

const defaultArk: ArkSettingsForm = {
  base_url: "https://ark.cn-beijing.volces.com/api/v3",
  protocol: "responses",
  model: "",
  concurrency: 3,
  timeout_seconds: 120,
  max_retries: 2,
  thinking: "disabled",
};

function toNumericDrafts(settings: ArkSettingsForm): NumericDrafts {
  return {
    concurrency: String(settings.concurrency),
    timeout_seconds: String(settings.timeout_seconds),
    max_retries: String(settings.max_retries),
  };
}

function parseBoundedInteger(value: string, minimum: number, maximum: number): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "请求失败，请检查本地服务。";
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const ark = useQuery({ queryKey: ["settings", "ark"], queryFn: api.getArkSettings });
  const feishuStatus = useQuery({
    queryKey: ["auth", "feishu"],
    queryFn: api.getFeishuStatus,
  });
  const [arkForm, setArkForm] = useState(defaultArk);
  const [numericDrafts, setNumericDrafts] = useState(() => toNumericDrafts(defaultArk));
  const [arkKey, setArkKey] = useState("");
  const [arkMessage, setArkMessage] = useState<string | null>(null);
  const [arkError, setArkError] = useState<string | null>(null);

  useEffect(() => {
    if (!ark.data) return;
    const { api_key_configured: _configured, ...publicSettings } = ark.data;
    setArkForm(publicSettings);
    setNumericDrafts(toNumericDrafts(publicSettings));
  }, [ark.data]);

  const saveArk = useMutation({
    mutationFn: async (payload: ArkSettingsForm) => {
      await api.saveArkSettings({ ...payload, api_key: arkKey || null });
      return api.testArk();
    },
    onSuccess: async (result) => {
      setArkKey("");
      setArkError(null);
      setArkMessage(`连接测试通过，耗时 ${result.latency_ms} ms`);
      await queryClient.invalidateQueries({ queryKey: ["settings", "ark"] });
    },
    onError: (error) => {
      setArkMessage(null);
      setArkError(errorMessage(error));
    },
  });

  const hasBlankNumericField = Object.values(numericDrafts).some((value) => !value.trim());

  function submitArk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hasBlankNumericField) return;

    const concurrency = parseBoundedInteger(numericDrafts.concurrency, 1, 500);
    if (concurrency === null) {
      setArkError("并发数必须是 1 到 500 之间的整数");
      return;
    }
    const timeoutSeconds = parseBoundedInteger(numericDrafts.timeout_seconds, 1, 600);
    if (timeoutSeconds === null) {
      setArkError("超时必须是 1 到 600 之间的整数");
      return;
    }
    const maxRetries = parseBoundedInteger(numericDrafts.max_retries, 0, 5);
    if (maxRetries === null) {
      setArkError("自动重试必须是 0 到 5 之间的整数");
      return;
    }

    const payload: ArkSettingsForm = {
      ...arkForm,
      concurrency,
      timeout_seconds: timeoutSeconds,
      max_retries: maxRetries,
    };
    setArkError(null);
    setArkMessage(null);
    saveArk.mutate(payload);
  }

  const loading = ark.isLoading || feishuStatus.isLoading;

  return (
    <main className="page settings-page">
      <header className="page-header compact-header">
        <p className="section-label">连接与凭据</p>
        <h1>设置</h1>
        <p>密钥只保存在系统钥匙串。浏览器和本地数据库只记录是否已配置。</p>
      </header>

      {loading ? (
        <div className="settings-grid" aria-label="正在加载设置">
          <div className="skeleton settings-skeleton" />
          <div className="skeleton settings-skeleton" />
        </div>
      ) : (
        <div className="settings-grid">
          <section className="settings-panel" aria-labelledby="ark-heading">
            <div className="panel-heading">
              <span className="icon-tile"><CloudArrowUp size={22} weight="bold" /></span>
              <div>
                <h2 id="ark-heading">火山方舟</h2>
                <p>每次请求只发送一张图片。</p>
              </div>
            </div>
            <form className="field-grid" onSubmit={submitArk} noValidate>
              <label className="field field-wide">
                <span>Base URL</span>
                <input
                  type="url"
                  disabled={saveArk.isPending}
                  value={arkForm.base_url}
                  onChange={(event) => setArkForm({ ...arkForm, base_url: event.target.value })}
                  required
                />
              </label>
              <label className="field">
                <span>协议</span>
                <select
                  disabled={saveArk.isPending}
                  value={arkForm.protocol}
                  onChange={(event) =>
                    setArkForm({
                      ...arkForm,
                      protocol: event.target.value as ArkSettings["protocol"],
                    })
                  }
                >
                  <option value="responses">Responses API</option>
                  <option value="chat">Chat Completions</option>
                </select>
              </label>
              <label className="field">
                <span>模型 / Endpoint ID</span>
                <input
                  disabled={saveArk.isPending}
                  value={arkForm.model}
                  onChange={(event) => setArkForm({ ...arkForm, model: event.target.value })}
                  required
                />
              </label>
              <label className="field field-wide">
                <span>API Key</span>
                <div className="secret-input-wrap">
                  <Key size={18} aria-hidden="true" />
                  <input
                    aria-label="API Key"
                    type="password"
                    autoComplete="new-password"
                    disabled={saveArk.isPending}
                    placeholder={ark.data?.api_key_configured ? "留空以保留现有密钥" : "输入 API Key"}
                    value={arkKey}
                    onChange={(event) => setArkKey(event.target.value)}
                  />
                </div>
                {ark.data?.api_key_configured && (
                  <small className="secure-state"><LockKey size={14} /> 已存入系统钥匙串</small>
                )}
              </label>
              <label className="field">
                <span>并发数</span>
                <input
                  type="number"
                  min={1}
                  max={500}
                  step={1}
                  required
                  disabled={saveArk.isPending}
                  value={numericDrafts.concurrency}
                  onChange={(event) =>
                    setNumericDrafts((current) => ({
                      ...current,
                      concurrency: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="field">
                <span>超时（秒）</span>
                <input
                  type="number"
                  min={1}
                  max={600}
                  step={1}
                  required
                  disabled={saveArk.isPending}
                  value={numericDrafts.timeout_seconds}
                  onChange={(event) =>
                    setNumericDrafts((current) => ({
                      ...current,
                      timeout_seconds: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="field">
                <span>自动重试</span>
                <input
                  type="number"
                  min={0}
                  max={5}
                  step={1}
                  required
                  disabled={saveArk.isPending}
                  value={numericDrafts.max_retries}
                  onChange={(event) =>
                    setNumericDrafts((current) => ({
                      ...current,
                      max_retries: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="field">
                <span>思考模式</span>
                <select
                  disabled={saveArk.isPending}
                  value={arkForm.thinking}
                  onChange={(event) =>
                    setArkForm({
                      ...arkForm,
                      thinking: event.target.value as ArkSettings["thinking"],
                    })
                  }
                >
                  <option value="disabled">关闭</option>
                  <option value="auto">自动</option>
                  <option value="enabled">开启</option>
                </select>
              </label>
              <div className="form-actions field-wide">
                <button
                  className="button primary-button"
                  disabled={saveArk.isPending || hasBlankNumericField}
                  type="submit"
                >
                  {saveArk.isPending ? "正在测试..." : "测试并保存"}
                </button>
                <span className={arkError ? "form-message error-text" : "form-message"} aria-live="polite">
                  {arkError ?? arkMessage}
                </span>
              </div>
            </form>
          </section>

          <section className="settings-panel" aria-labelledby="feishu-heading">
            <div className="panel-heading">
              <span className="icon-tile"><LinkSimple size={22} weight="bold" /></span>
              <div>
                <h2 id="feishu-heading">飞书报告</h2>
                <p>使用你的身份创建电子表格并回填巡检图片。</p>
              </div>
            </div>
            <div className="connection-status">
              <span>授权状态</span>
              <strong className={feishuStatus.data?.connected ? "is-connected" : ""}>
                {feishuStatus.data?.connected ? "已连接" : "未连接"}
              </strong>
            </div>
            <dl className="integration-details">
              <div>
                <dt>已连接账号</dt>
                <dd>{feishuStatus.data?.user_name ?? "—"}</dd>
              </div>
              <div>
                <dt>授权方式</dt>
                <dd>Codex 对话授权</dd>
              </div>
              <div>
                <dt>飞书应用</dt>
                <dd>{feishuStatus.data?.app_name ?? "—"}</dd>
              </div>
              <div className="integration-detail-wide">
                <dt>报告位置</dt>
                <dd>{feishuStatus.data?.destination_path || "未配置"}</dd>
              </div>
            </dl>
            <div className="form-actions split-actions">
              <button
                className="button quiet-button"
                type="button"
                disabled={feishuStatus.isFetching}
                onClick={() => void feishuStatus.refetch()}
              >
                <ArrowClockwise size={18} />
                {feishuStatus.isFetching ? "刷新中..." : "刷新状态"}
              </button>
              {feishuStatus.data?.reference_url && (
                <a
                  className="button quiet-button"
                  href={feishuStatus.data.reference_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开参考表格 <ArrowSquareOut size={16} />
                </a>
              )}
            </div>
            {!feishuStatus.data?.connected && (
              <p className="integration-hint">
                请在 Codex 对话中输入“授权飞书”，完成后刷新状态。
              </p>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

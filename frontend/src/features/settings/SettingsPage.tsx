import {
  ArrowSquareOut,
  CheckCircle,
  CloudArrowUp,
  Key,
  LinkSimple,
  LockKey,
  PlugsConnected,
  SignOut,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { ArkSettings } from "../../api/types";

const defaultArk: Omit<ArkSettings, "api_key_configured"> = {
  base_url: "https://ark.cn-beijing.volces.com/api/v3",
  protocol: "responses",
  model: "",
  concurrency: 3,
  timeout_seconds: 120,
  max_retries: 2,
  thinking: "disabled",
};

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "请求失败，请检查本地服务。";
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const ark = useQuery({ queryKey: ["settings", "ark"], queryFn: api.getArkSettings });
  const feishu = useQuery({
    queryKey: ["settings", "feishu"],
    queryFn: api.getFeishuSettings,
  });
  const feishuStatus = useQuery({
    queryKey: ["auth", "feishu"],
    queryFn: api.getFeishuStatus,
  });
  const [arkForm, setArkForm] = useState(defaultArk);
  const [arkKey, setArkKey] = useState("");
  const [arkMessage, setArkMessage] = useState<string | null>(null);
  const [arkError, setArkError] = useState<string | null>(null);
  const [feishuForm, setFeishuForm] = useState({ app_id: "", folder_token: "" });
  const [feishuSecret, setFeishuSecret] = useState("");
  const [feishuMessage, setFeishuMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!ark.data) return;
    const { api_key_configured: _configured, ...publicSettings } = ark.data;
    setArkForm(publicSettings);
  }, [ark.data]);

  useEffect(() => {
    if (!feishu.data) return;
    setFeishuForm({ app_id: feishu.data.app_id, folder_token: feishu.data.folder_token });
  }, [feishu.data]);

  const saveArk = useMutation({
    mutationFn: async () => {
      await api.saveArkSettings({ ...arkForm, api_key: arkKey || null });
      return api.testArk();
    },
    onSuccess: (result) => {
      setArkKey("");
      setArkError(null);
      setArkMessage(`连接测试通过，耗时 ${result.latency_ms} ms`);
      void queryClient.invalidateQueries({ queryKey: ["settings", "ark"] });
    },
    onError: (error) => {
      setArkMessage(null);
      setArkError(errorMessage(error));
    },
  });

  const connectFeishu = useMutation({
    mutationFn: async () => {
      await api.saveFeishuSettings({ ...feishuForm, app_secret: feishuSecret || null });
      return api.startFeishuAuth();
    },
    onSuccess: ({ authorization_url }) => {
      setFeishuSecret("");
      setFeishuMessage("已打开飞书授权页面");
      void queryClient.invalidateQueries({ queryKey: ["settings", "feishu"] });
      window.open(authorization_url, "_blank", "noopener,noreferrer");
    },
    onError: (error) => setFeishuMessage(errorMessage(error)),
  });

  const disconnect = useMutation({
    mutationFn: api.disconnectFeishu,
    onSuccess: () => {
      setFeishuMessage("已断开飞书授权");
      void queryClient.invalidateQueries({ queryKey: ["auth", "feishu"] });
    },
    onError: (error) => setFeishuMessage(errorMessage(error)),
  });

  function submitArk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (arkForm.concurrency < 1 || arkForm.concurrency > 5) {
      setArkError("并发数必须在 1 到 5 之间");
      return;
    }
    setArkError(null);
    setArkMessage(null);
    saveArk.mutate();
  }

  const callbackState = searchParams.get("feishu");
  const loading = ark.isLoading || feishu.isLoading || feishuStatus.isLoading;

  return (
    <main className="page settings-page">
      <header className="page-header compact-header">
        <p className="section-label">连接与凭据</p>
        <h1>设置</h1>
        <p>密钥只保存在系统钥匙串。浏览器和本地数据库只记录是否已配置。</p>
      </header>

      {callbackState === "connected" && (
        <div className="notice success-notice" role="status">
          <CheckCircle size={20} weight="fill" /> 飞书授权已完成
        </div>
      )}
      {callbackState === "error" && (
        <div className="notice error-notice" role="alert">飞书授权失败，请重新连接。</div>
      )}

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
                  value={arkForm.base_url}
                  onChange={(event) => setArkForm({ ...arkForm, base_url: event.target.value })}
                  required
                />
              </label>
              <label className="field">
                <span>协议</span>
                <select
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
                  max={5}
                  value={arkForm.concurrency}
                  onChange={(event) =>
                    setArkForm({ ...arkForm, concurrency: Number(event.target.value) })
                  }
                />
              </label>
              <label className="field">
                <span>超时（秒）</span>
                <input
                  type="number"
                  min={1}
                  max={600}
                  value={arkForm.timeout_seconds}
                  onChange={(event) =>
                    setArkForm({ ...arkForm, timeout_seconds: Number(event.target.value) })
                  }
                />
              </label>
              <label className="field">
                <span>自动重试</span>
                <input
                  type="number"
                  min={0}
                  max={5}
                  value={arkForm.max_retries}
                  onChange={(event) =>
                    setArkForm({ ...arkForm, max_retries: Number(event.target.value) })
                  }
                />
              </label>
              <label className="field">
                <span>思考模式</span>
                <select
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
                <button className="button primary-button" disabled={saveArk.isPending} type="submit">
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
                <p>使用你的身份创建文档和上传缩略图。</p>
              </div>
            </div>
            <div className="connection-status">
              <span>授权状态</span>
              <strong>{feishuStatus.data?.connected ? "已连接" : "未连接"}</strong>
            </div>
            <div className="field-grid">
              <label className="field field-wide">
                <span>App ID</span>
                <input
                  value={feishuForm.app_id}
                  onChange={(event) =>
                    setFeishuForm({ ...feishuForm, app_id: event.target.value })
                  }
                />
              </label>
              <label className="field field-wide">
                <span>App Secret</span>
                <div className="secret-input-wrap">
                  <Key size={18} aria-hidden="true" />
                  <input
                    aria-label="App Secret"
                    type="password"
                    autoComplete="new-password"
                    placeholder={
                      feishu.data?.app_secret_configured ? "留空以保留现有密钥" : "输入 App Secret"
                    }
                    value={feishuSecret}
                    onChange={(event) => setFeishuSecret(event.target.value)}
                  />
                </div>
                {feishu.data?.app_secret_configured && (
                  <small className="secure-state"><LockKey size={14} /> 已存入系统钥匙串</small>
                )}
              </label>
              <label className="field field-wide">
                <span>报告文件夹 Token</span>
                <input
                  value={feishuForm.folder_token}
                  onChange={(event) =>
                    setFeishuForm({ ...feishuForm, folder_token: event.target.value })
                  }
                />
              </label>
              <label className="field field-wide">
                <span>OAuth 回调地址</span>
                <input value={feishu.data?.callback_url ?? ""} readOnly />
              </label>
              <div className="form-actions field-wide split-actions">
                <button
                  className="button primary-button"
                  type="button"
                  disabled={connectFeishu.isPending}
                  onClick={() => connectFeishu.mutate()}
                >
                  <PlugsConnected size={18} weight="bold" />
                  {connectFeishu.isPending ? "正在准备授权..." : "连接飞书"}
                </button>
                {feishuStatus.data?.connected && (
                  <button
                    className="button quiet-button"
                    type="button"
                    onClick={() => disconnect.mutate()}
                  >
                    <SignOut size={18} /> 断开
                  </button>
                )}
                <span className="form-message" aria-live="polite">{feishuMessage}</span>
              </div>
            </div>
            <a
              className="docs-link"
              href="https://open.feishu.cn/document/"
              target="_blank"
              rel="noreferrer"
            >
              查看飞书开放平台配置说明 <ArrowSquareOut size={16} />
            </a>
          </section>
        </div>
      )}
    </main>
  );
}

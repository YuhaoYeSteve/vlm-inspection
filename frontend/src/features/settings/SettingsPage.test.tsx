import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import { server } from "../../test/server";
import { SettingsPage } from "./SettingsPage";

const arkSettings = {
  base_url: "https://ark.cn-beijing.volces.com/api/v3",
  protocol: "responses",
  model: "seed-vision",
  concurrency: 3,
  timeout_seconds: 120,
  max_retries: 2,
  thinking: "disabled",
  api_key_configured: true,
};

const feishuStatus = {
  configured: true,
  connected: true,
  user_name: "测试用户",
  app_name: "示例应用",
  identity: "user",
  token_status: "valid",
  expires_at: "2030-01-01T00:00:00Z",
  destination_path: "我的空间 / 示例目录 / 巡检报告",
  reference_url: "https://example.feishu.cn/docx/reference",
  error_code: null,
};

function useDefaultHandlers() {
  const statusHandler = vi.fn(() => HttpResponse.json(feishuStatus));
  server.use(
    http.get("*/api/settings/ark", () => HttpResponse.json(arkSettings)),
    http.put("*/api/settings/ark", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...arkSettings, ...body, api_key_configured: true });
    }),
    http.post("*/api/settings/ark/test", () =>
      HttpResponse.json({ ok: true, protocol: "responses", model: "seed-vision", latency_ms: 81 }),
    ),
    http.get("*/api/auth/feishu/status", statusHandler),
  );
  return { statusHandler };
}

function renderSettings(entry = "/settings") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("shows masked configured states without rendering secrets", async () => {
  useDefaultHandlers();
  renderSettings();

  expect(await screen.findAllByText("已存入系统钥匙串")).toHaveLength(1);
  expect(screen.getByLabelText("API Key")).toHaveValue("");
  expect(screen.queryByLabelText("App Secret")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("ark-secret-value");
});

test("replaces the submitted Ark key with configured state", async () => {
  useDefaultHandlers();
  const user = userEvent.setup();
  renderSettings();
  await screen.findByDisplayValue("seed-vision");

  await user.type(screen.getByLabelText("API Key"), "ark-secret-value");
  await user.click(screen.getByRole("button", { name: "测试并保存" }));

  expect(await screen.findByText(/连接测试通过/)).toBeInTheDocument();
  expect(screen.queryByDisplayValue("ark-secret-value")).not.toBeInTheDocument();
});

test("validates concurrency before sending", async () => {
  useDefaultHandlers();
  const user = userEvent.setup();
  renderSettings();
  const concurrency = await screen.findByLabelText("并发数");

  await user.clear(concurrency);
  await user.type(concurrency, "6");
  await user.click(screen.getByRole("button", { name: "测试并保存" }));

  expect(await screen.findByText("并发数必须在 1 到 5 之间")).toBeInTheDocument();
});

test("renders Feishu account and destination without credential inputs", async () => {
  useDefaultHandlers();
  renderSettings();

  expect(await screen.findByText("测试用户")).toBeInTheDocument();
  expect(screen.getByText("示例应用")).toBeInTheDocument();
  expect(screen.getByText(feishuStatus.destination_path)).toBeInTheDocument();
  expect(screen.getByText("使用你的身份创建电子表格并回填巡检图片。")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /打开参考表格/ })).toHaveAttribute(
    "href",
    feishuStatus.reference_url,
  );
  expect(screen.queryByText("App ID")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("App Secret")).not.toBeInTheDocument();
  expect(screen.queryByText("报告文件夹 Token")).not.toBeInTheDocument();
  expect(screen.queryByText("OAuth 回调地址")).not.toBeInTheDocument();
});

test("refreshes conversation authorization status", async () => {
  const { statusHandler } = useDefaultHandlers();
  const user = userEvent.setup();
  renderSettings();
  await screen.findByText("测试用户");
  expect(statusHandler).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: "刷新状态" }));

  await waitFor(() => expect(statusHandler).toHaveBeenCalledTimes(2));
});

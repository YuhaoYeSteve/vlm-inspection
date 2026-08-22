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

const feishuSettings = {
  app_id: "cli_example",
  folder_token: "folder-1",
  callback_url: "http://127.0.0.1:8000/api/auth/feishu/callback",
  app_secret_configured: true,
};

function useDefaultHandlers() {
  server.use(
    http.get("*/api/settings/ark", () => HttpResponse.json(arkSettings)),
    http.put("*/api/settings/ark", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...arkSettings, ...body, api_key_configured: true });
    }),
    http.post("*/api/settings/ark/test", () =>
      HttpResponse.json({ ok: true, protocol: "responses", model: "seed-vision", latency_ms: 81 }),
    ),
    http.get("*/api/settings/feishu", () => HttpResponse.json(feishuSettings)),
    http.put("*/api/settings/feishu", () => HttpResponse.json(feishuSettings)),
    http.get("*/api/auth/feishu/status", () =>
      HttpResponse.json({ configured: true, connected: false, expires_at: null }),
    ),
    http.get("*/api/auth/feishu/start", () =>
      HttpResponse.json({ authorization_url: "https://accounts.feishu.cn/authorize?state=test" }),
    ),
    http.delete("*/api/auth/feishu/disconnect", () => new HttpResponse(null, { status: 204 })),
  );
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

  expect(await screen.findAllByText("已存入系统钥匙串")).toHaveLength(2);
  expect(screen.getByLabelText("API Key")).toHaveValue("");
  expect(screen.getByLabelText("App Secret")).toHaveValue("");
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

test("opens Feishu authorization and renders callback success", async () => {
  useDefaultHandlers();
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  const user = userEvent.setup();
  renderSettings("/settings?feishu=connected");

  expect(await screen.findByText("飞书授权已完成")).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "连接飞书" }));

  await waitFor(() =>
    expect(open).toHaveBeenCalledWith(
      "https://accounts.feishu.cn/authorize?state=test",
      "_blank",
      "noopener,noreferrer",
    ),
  );
});

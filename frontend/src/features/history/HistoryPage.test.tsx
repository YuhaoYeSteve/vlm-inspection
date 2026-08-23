import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";

import type { TaskSummary } from "../../api/types";
import { server } from "../../test/server";
import { HistoryPage } from "./HistoryPage";

const tasks: TaskSummary[] = [
  {
    id: "older",
    name: "早班巡检",
    status: "completed",
    template_name: "门店仪容仪表",
    template_version: 2,
    model: "seed-1-6",
    created_at: "2026-08-20T02:00:00Z",
    started_at: "2026-08-20T02:01:00Z",
    completed_at: "2026-08-20T02:10:00Z",
    counts: { pass: 8, fail: 1, unverifiable: 1, failed: 0 },
    token_usage: {
      availability: "untracked",
      input_tokens: 0,
      cached_input_tokens: 0,
      uncached_input_tokens: 0,
      output_tokens: 0,
      reasoning_tokens: 0,
      total_tokens: 0,
      known_call_count: 0,
      unknown_call_count: 0,
    },
    report_id: "report-1",
    report_status: "succeeded",
    report_url: "https://example.feishu.cn/docx/report-1",
  },
  {
    id: "newer",
    name: "晚班巡检",
    status: "review_required",
    template_name: "餐饮后厨卫生",
    template_version: 1,
    model: "seed-1-6",
    created_at: "2026-08-22T10:00:00Z",
    started_at: "2026-08-22T10:01:00Z",
    completed_at: null,
    counts: { pass: 4, fail: 2, unverifiable: 0, failed: 1 },
    token_usage: {
      availability: "partial",
      input_tokens: 1101,
      cached_input_tokens: 420,
      uncached_input_tokens: 681,
      output_tokens: 183,
      reasoning_tokens: 0,
      total_tokens: 1284,
      known_call_count: 2,
      unknown_call_count: 1,
    },
    report_id: "report-2",
    report_status: "failed",
    report_url: null,
  },
];

function renderHistory() {
  server.use(http.get("*/api/tasks", () => HttpResponse.json(tasks)));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("shows newest tasks first with status and result counts", async () => {
  renderHistory();

  const rows = await screen.findAllByRole("row");
  expect(within(rows[1]).getByText("晚班巡检")).toBeInTheDocument();
  expect(within(rows[1]).getByText("等待确认")).toBeInTheDocument();
  expect(within(rows[1]).getByText("4 / 2 / 0 / 1")).toBeInTheDocument();
  expect(within(rows[1]).getByText("已知 1,284 Token")).toBeInTheDocument();
  expect(within(rows[1]).getByText("1 次未知")).toBeInTheDocument();
  expect(within(rows[2]).getByText("早班巡检")).toBeInTheDocument();
  expect(within(rows[2]).getByText("未采集")).toBeInTheDocument();
});

test("links back to task detail and opens a completed Feishu report safely", async () => {
  renderHistory();

  const taskLink = await screen.findByRole("link", { name: "查看早班巡检" });
  expect(taskLink).toHaveAttribute("href", "/tasks/older");
  const reportLink = screen.getByRole("link", { name: "打开早班巡检飞书文档" });
  expect(reportLink).toHaveAttribute("href", "https://example.feishu.cn/docx/report-1");
  expect(reportLink).toHaveAttribute("target", "_blank");
  expect(reportLink).toHaveAttribute("rel", "noreferrer");
});

test("retries a failed report with explicit failed-image inclusion", async () => {
  let requestedTask = "";
  let includeFailures = false;
  server.use(
    http.post("*/api/tasks/:id/report", async ({ params, request }) => {
      requestedTask = String(params.id);
      includeFailures = Boolean(((await request.json()) as { include_failures: boolean }).include_failures);
      return HttpResponse.json({ export_id: "report-3", status: "accepted" }, { status: 202 });
    }),
  );
  const user = userEvent.setup();
  renderHistory();

  await user.click(await screen.findByRole("button", { name: "重试晚班巡检报告" }));

  await waitFor(() => expect(requestedTask).toBe("newer"));
  expect(includeFailures).toBe(true);
  expect(screen.getByText("报告重试已提交")).toBeInTheDocument();
});

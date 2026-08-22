import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { TaskDetail } from "../../api/types";
import { server } from "../../test/server";
import { TaskPage } from "./TaskPage";

const finding = {
  rule_id: "badge",
  rule_name: "工牌佩戴",
  status: "fail" as const,
  observation: "左胸未见工牌",
  visual_evidence: "左胸区域无遮挡，未出现工牌",
};

const taskDetail: TaskDetail = {
  id: "task-1",
  name: "门店巡检",
  status: "review_required",
  template_name: "通用员工仪容仪表",
  template_version: 1,
  model: "seed-test",
  created_at: "2026-08-22T10:00:00Z",
  started_at: "2026-08-22T10:01:00Z",
  completed_at: null,
  counts: { pass: 1, fail: 1, unverifiable: 0, failed: 1 },
  report_id: null,
  report_status: null,
  report_url: null,
  template_snapshot: {},
  model_snapshot: {},
  images: [
    {
      id: "image-1",
      position: 0,
      original_name: "IMG_001.png",
      sha256: "one",
      status: "succeeded",
      attempt_count: 1,
      error_code: null,
      error_message: null,
      original_url: "/api/image-1/original",
      thumbnail_url: "/api/image-1/thumb",
      result: {
        schema_version: 1,
        image_valid: true,
        unverifiable_reason: null,
        verdict: "pass",
        summary: "仪容项目通过",
        findings: [{ ...finding, status: "pass", observation: "工牌清晰可见" }],
      },
    },
    {
      id: "image-2",
      position: 1,
      original_name: "IMG_002.png",
      sha256: "two",
      status: "succeeded",
      attempt_count: 1,
      error_code: null,
      error_message: null,
      original_url: "/api/image-2/original",
      thumbnail_url: "/api/image-2/thumb",
      result: {
        schema_version: 1,
        image_valid: true,
        unverifiable_reason: null,
        verdict: "fail",
        summary: "工牌缺失",
        findings: [finding],
      },
    },
    {
      id: "image-3",
      position: 2,
      original_name: "IMG_003.png",
      sha256: "three",
      status: "failed",
      attempt_count: 3,
      error_code: "timeout",
      error_message: "模型请求超时",
      original_url: "/api/image-3/original",
      thumbnail_url: "/api/image-3/thumb",
      result: null,
    },
  ],
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, (event: MessageEvent) => void>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, listener as (event: MessageEvent) => void);
  }

  close() {}

  emit(type: string, lastEventId = "1") {
    this.listeners.get(type)?.(new MessageEvent(type, { data: "{}", lastEventId }));
  }
}

function renderTask(detail: TaskDetail = taskDetail, installHandler = true) {
  if (installHandler) {
    server.use(http.get("*/api/tasks/:id", () => HttpResponse.json(detail)));
  }
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TaskPage taskId={detail.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => vi.unstubAllGlobals());

test("keeps gallery and single-image evidence visible together", async () => {
  const user = userEvent.setup();
  renderTask();

  expect(await screen.findAllByRole("img", { name: /巡检图片/ })).toHaveLength(3);
  await user.click(screen.getByRole("button", { name: "查看 IMG_002.png" }));

  expect(screen.getByRole("img", { name: "IMG_002.png 大图" })).toBeInTheDocument();
  expect(screen.getByText("左胸区域无遮挡，未出现工牌")).toBeInTheDocument();
});

test("filters statuses and retries only failed images", async () => {
  let retried = "";
  server.use(
    http.post("*/api/tasks/:taskId/images/:imageId/retry", ({ params }) => {
      retried = String(params.imageId);
      return HttpResponse.json({ task_id: params.taskId, status: "accepted" }, { status: 202 });
    }),
  );
  const user = userEvent.setup();
  renderTask();
  await screen.findByText("IMG_001.png");

  await user.click(screen.getByRole("button", { name: "执行失败 1" }));
  expect(screen.queryByText("IMG_001.png")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看 IMG_003.png" }));
  await user.click(screen.getByRole("button", { name: "重试此图片" }));

  await waitFor(() => expect(retried).toBe("image-3"));
});

test("guards the image limit before sending upload", async () => {
  const user = userEvent.setup();
  renderTask({ ...taskDetail, status: "draft", images: [], counts: { pass: 0, fail: 0, unverifiable: 0, failed: 0 } });
  await screen.findByText("门店巡检");
  const files = Array.from(
    { length: 101 },
    (_, index) => new File([String(index)], `${index}.png`, { type: "image/png" }),
  );

  await user.upload(screen.getByLabelText("上传图片"), files);

  expect(await screen.findByText("单个任务最多 100 张图片")).toBeInTheDocument();
});

test("asks for confirmation before exporting failed items", async () => {
  let includeFailures = false;
  server.use(
    http.post("*/api/tasks/:id/report", async ({ request }) => {
      includeFailures = Boolean(((await request.json()) as { include_failures: boolean }).include_failures);
      return HttpResponse.json({ export_id: "export-1", status: "accepted" }, { status: 202 });
    }),
  );
  const user = userEvent.setup();
  renderTask();
  await screen.findByText("门店巡检");

  await user.click(screen.getByRole("button", { name: "带失败项生成报告" }));
  const dialog = screen.getByRole("dialog", { name: "确认生成报告" });
  await user.click(within(dialog).getByRole("button", { name: "确认生成" }));

  await waitFor(() => expect(includeFailures).toBe(true));
});

test("refetches authoritative state after an SSE terminal event", async () => {
  let fetchCount = 0;
  server.use(
    http.get("*/api/tasks/:id", () => {
      fetchCount += 1;
      return HttpResponse.json(taskDetail);
    }),
  );
  renderTask(taskDetail, false);
  await screen.findByText("门店巡检");
  const source = FakeEventSource.instances.at(-1);
  expect(source).toBeDefined();

  source?.emit("image.succeeded", "7");

  await waitFor(() => expect(fetchCount).toBeGreaterThan(1));
});

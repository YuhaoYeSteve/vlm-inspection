import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";

import { server } from "../../test/server";
import { TemplatesPage } from "./TemplatesPage";

const baseTemplate = {
  id: "template-general",
  slug: "general",
  name: "通用员工仪容仪表",
  description: "员工可见项目巡检",
  enabled: true,
  version: 1,
  version_id: "version-1",
  created_at: "2026-08-22T10:00:00Z",
  system_prompt: "只判断图片中清晰可见的事实，遮挡时使用 not_visible。",
  task_prompt: "逐项检查仪容仪表。",
  rules: [
    { rule_id: "badge", name: "工牌佩戴", text: "左胸清晰可见时检查工牌。" },
    { rule_id: "uniform", name: "制服整洁", text: "检查明显污渍和破损。" },
  ],
  model_params: { temperature: 0.1, max_output_tokens: 4096 },
};

const templates = [
  baseTemplate,
  { ...baseTemplate, id: "template-manager", slug: "manager", name: "店总仪容仪表" },
  { ...baseTemplate, id: "template-chef", slug: "chef", name: "厨师仪容规范" },
];

function handlers() {
  server.use(
    http.get("*/api/templates", () => HttpResponse.json(templates)),
    http.get("*/api/templates/:id", ({ params }) =>
      HttpResponse.json(templates.find((item) => item.id === params.id) ?? baseTemplate),
    ),
    http.put("*/api/templates/:id", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...baseTemplate, ...body, version: 2, version_id: "version-2" });
    }),
    http.post("*/api/templates/:id/test", () =>
      HttpResponse.json({
        schema_version: 1,
        image_valid: true,
        unverifiable_reason: null,
        verdict: "fail",
        summary: "工牌缺失",
        overall_confidence: 0.94,
        findings: [
          {
            rule_id: "badge",
            rule_name: "工牌佩戴",
            status: "fail",
            observation: "左胸未见工牌",
            visual_evidence: "左胸区域无遮挡，未出现工牌",
            confidence: 0.94,
          },
        ],
      }),
    ),
  );
}

function renderTemplates() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("saving an edited template displays the new version", async () => {
  handlers();
  const user = userEvent.setup();
  renderTemplates();

  await user.click(await screen.findByRole("button", { name: "通用员工仪容仪表" }));
  await user.type(screen.getByLabelText("任务补充说明"), " 重点检查工牌");
  await user.click(screen.getByRole("button", { name: "保存新版本" }));

  expect(await screen.findByText("当前版本 v2")).toBeInTheDocument();
});

test("validates rule ids and supports adding removing and reordering", async () => {
  handlers();
  const user = userEvent.setup();
  renderTemplates();
  await user.click(await screen.findByRole("button", { name: "通用员工仪容仪表" }));

  await user.click(screen.getByRole("button", { name: "添加规则" }));
  const rules = screen.getAllByTestId("rule-row");
  expect(rules).toHaveLength(3);
  await user.type(within(rules[2]).getByLabelText("规则 ID"), "badge");
  await user.type(within(rules[2]).getByLabelText("规则名称"), "重复工牌");
  await user.type(within(rules[2]).getByLabelText("检查要求"), "重复规则");
  await user.click(screen.getByRole("button", { name: "保存新版本" }));
  expect(await screen.findByText("规则 ID 必须唯一")).toBeInTheDocument();

  await user.click(within(rules[2]).getByRole("button", { name: "删除规则" }));
  const remaining = screen.getAllByTestId("rule-row");
  await user.click(within(remaining[1]).getByRole("button", { name: "上移规则" }));
  expect(within(screen.getAllByTestId("rule-row")[0]).getByDisplayValue("uniform")).toBeInTheDocument();
});

test("previews the final prompt and renders a single image result", async () => {
  handlers();
  const user = userEvent.setup();
  renderTemplates();
  await user.click(await screen.findByRole("button", { name: "厨师仪容规范" }));

  await user.click(screen.getByRole("button", { name: "预览最终提示词" }));
  const dialog = screen.getByRole("dialog", { name: "最终提示词预览" });
  expect(within(dialog).getByText(/not_visible/)).toBeInTheDocument();
  expect(within(dialog).getByText(/badge/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "关闭" }));

  const file = new File(["image"], "chef.png", { type: "image/png" });
  await user.upload(screen.getByLabelText("选择单张测试图片"), file);
  await user.click(screen.getByRole("button", { name: "运行单图测试" }));

  expect(await screen.findByText("工牌缺失")).toBeInTheDocument();
  expect(screen.getByText("左胸区域无遮挡，未出现工牌")).toBeInTheDocument();
});

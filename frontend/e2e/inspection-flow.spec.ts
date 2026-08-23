import { expect, test } from "@playwright/test";

test("configures, runs three images, and opens the Feishu report", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("模型 / Endpoint ID").fill("doubao-seed-test");
  await page.getByLabel("API Key").fill("E2E_ARK_SECRET");
  await page.getByRole("button", { name: "测试并保存" }).click();
  await expect(page.getByText("连接测试通过", { exact: false })).toBeVisible();

  await page.goto("/tasks/new");
  await page.getByLabel("任务名称").fill("E2E 巡检");
  await page.getByLabel("上传图片").setInputFiles([
    "e2e/fixtures/one.png",
    "e2e/fixtures/two.png",
    "e2e/fixtures/three.png",
  ]);
  await page.getByRole("button", { name: "开始任务" }).click();

  await expect(page.getByText("飞书报告已生成")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("link", { name: "打开飞书表格" })).toHaveAttribute(
    "href",
    "https://example.feishu.cn/sheets/report-1",
  );
  const taskUsage = page.getByRole("region", { name: "任务 Token 用量" });
  const totalMetric = taskUsage.getByText("总 Token", { exact: true }).locator("..");
  await expect(totalMetric.getByText("36", { exact: true })).toBeVisible();
  const cachedMetric = taskUsage.getByText("缓存命中", { exact: true }).locator("..");
  await expect(cachedMetric.getByText("12", { exact: true })).toBeVisible();
  const browserStorage = await page.evaluate(() =>
    JSON.stringify({ localStorage: { ...localStorage }, sessionStorage: { ...sessionStorage } }),
  );
  expect(browserStorage).not.toContain("E2E_ARK_SECRET");
});

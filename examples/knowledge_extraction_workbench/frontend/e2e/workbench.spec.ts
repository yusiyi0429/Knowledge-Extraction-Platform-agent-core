import { expect, test, type Page, type TestInfo } from "@playwright/test";

const unsafeSkillZip = Buffer.from(
  "UEsDBBQAAAAAAEKUBl3qGeo9OAAAADgAAAAIAAAAU0tJTEwubWQtLS0KbmFtZTogdW5zYWZlCmRlc2NyaXB0aW9uOiB1bnNhZmUgdHJhdmVyc2FsIHRlc3QKLS0tClBLAwQUAAAAAABClAZdjrDoJQYAAAAGAAAADgAAAC4uL291dHNpZGUudHh0ZXNjYXBlUEsBAhQDFAAAAAAAQpQGXeoZ6j04AAAAOAAAAAgAAAAAAAAAAAAAAIABAAAAAFNLSUxMLm1kUEsBAhQDFAAAAAAAQpQGXY6w6CUGAAAABgAAAA4AAAAAAAAAAAAAAIABXgAAAC4uL291dHNpZGUudHh0UEsFBgAAAAACAAIAcgAAAJAAAAAAAA==",
  "base64",
);

async function login(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "进入知识萃取工作台" })).toBeVisible();
  await page.getByRole("button", { name: "进入本机工作台" }).click();
  await expect(page.getByRole("heading", { name: /知识萃取工作台/ })).toBeVisible();
}

function collectBrowserErrors(page: Page, allowResponse: (url: string, status: number) => boolean = () => false): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400 && !allowResponse(response.url(), response.status())) {
      errors.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
  });
  return errors;
}

test("直接新建场景，完成萃取、建议、五类资产、发布与归档", async ({ page }, testInfo: TestInfo) => {
  const consoleErrors = collectBrowserErrors(page, (url, status) => status === 422 && new URL(url).pathname === "/api/v1/skills");
  const sceneName = `差旅费用审核-${testInfo.project.name}`;
  await login(page);

  await page.getByRole("button", { name: "新建场景" }).click();
  await page.getByLabel(/场景名称/).fill(sceneName);
  await page.getByLabel("场景描述").fill("从制度、流程和案例中沉淀差旅审核知识");
  await page.getByLabel("萃取目标").fill("生成规则、流程、QA、Skill 与评测集");
  await page.getByRole("button", { name: "创建并进入" }).click();
  await expect(page.getByText(sceneName, { exact: true })).toBeVisible();

  const materialText = [
    "当员工提交差旅申请时，必须填写出差目的、预算和成本中心；预算超过一万元时由部门负责人复核。",
    "财务审核人员应核对发票抬头、金额和行程日期；资料不完整时退回申请人补充，不得直接通过。",
    "国际差旅需要额外提交邀请函和合规说明，涉及敏感地区时转交合规负责人进行人工确认。",
  ].join("\n").repeat(18);
  await page.locator('.upload-strip input[type="file"]').setInputFiles({
    name: "travel-policy.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(materialText),
  });
  await expect(page.getByText("travel-policy.txt", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "进入知识萃取" }).click();
  await page.getByRole("button", { name: "启动知识萃取" }).click();
  await expect(page.getByRole("heading", { name: "知识研判文档" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel("知识研判 Markdown")).toContainText("规则清单");

  await page.getByRole("button", { name: "生成 AI 建议" }).click();
  await expect(page.getByRole("heading", { name: "建议补充审计闭环" })).toBeVisible();
  await page.getByRole("button", { name: "采纳建议" }).click();
  await expect(page.getByRole("status")).toContainText("已采纳建议并生成新修订。");
  await expect(page.getByLabel("知识研判 Markdown")).toContainText("复核要求");

  await page.getByRole("button", { name: "进入资产生成" }).click();
  await page.getByRole("button", { name: "生成五类资产" }).click();
  await expect(page.getByRole("heading", { name: "交付资产已齐备" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("合成评测集", { exact: false })).toBeVisible();
  await expect(page.getByText("待评测", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByRole("heading", { name: "v1 已发布" })).toBeVisible();

  await page.getByTitle("模型接入").click();
  await page.getByRole("button", { name: "新增模型" }).click();
  await page.getByLabel(/连接名称/).fill(`浏览器密钥边界-${testInfo.project.name}`);
  await page.getByLabel(/模型名称/).fill("deepseek-test-model");
  await page.getByLabel(/API 地址/).fill("https://example.invalid/v1");
  await page.getByLabel(/API Key/).fill("e2e-secret-must-never-return");
  await page.getByRole("button", { name: "保存连接" }).click();
  await expect(page.getByText("••••••••••••", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("e2e-secret-must-never-return");
  await page.getByLabel(new RegExp(`编辑浏览器密钥边界-${testInfo.project.name}`)).click();
  await expect(page.getByLabel(/API Key/)).toHaveValue("");
  await page.getByLabel("关闭").click();

  await page.getByTitle("智能体与 Skill").click();
  await page.getByRole("button", { name: /Skill 库/ }).click();
  await page.locator('input[type="file"][accept=".zip"]').setInputFiles({
    name: "unsafe.zip",
    mimeType: "application/zip",
    buffer: unsafeSkillZip,
  });
  await expect(page.getByRole("alert")).toContainText("Skill 包包含不安全路径。");

  await page.getByTitle("工作台").click();
  const sceneCard = page.locator("article.scene-card").filter({ hasText: sceneName });
  await expect(sceneCard).toBeVisible();
  await sceneCard.getByLabel(`归档 ${sceneName}`).click();
  await page.getByRole("button", { name: "确认归档" }).click();
  await expect(sceneCard).toHaveCount(0);

  await page.screenshot({ path: testInfo.outputPath("published-workbench.png"), fullPage: true });
  expect(consoleErrors).toEqual([]);
});

test("场景探索公平分析素材并带入新场景", async ({ page }, testInfo: TestInfo) => {
  const consoleErrors = collectBrowserErrors(page);
  await login(page);
  await page.getByRole("button", { name: "场景探索" }).click();
  await page.getByLabel("探索目标").fill("识别退款审批、异常升级和风险复核场景");
  const materialText = (
    "当客户提交大额退款申请时，客服需要核对订单状态、支付渠道和退款原因，超过授权额度时提交主管复核。\n" +
    "资料不完整时退回补充；发现重复退款或异常账户时，暂停自动处理并转交风险人员人工确认。\n"
  ).repeat(24);
  await page.locator('.explore-inputs input[type="file"]').setInputFiles({
    name: `refund-${testInfo.project.name}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(materialText),
  });
  await expect(page.getByText(`refund-${testInfo.project.name}.txt`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "分析候选场景" }).click();
  await expect(page.getByRole("button", { name: "带入新场景" }).first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "带入新场景" }).first().click();
  await expect(page.getByText("Step 01 · 场景与素材", { exact: true })).toBeVisible();
  await expect(page.getByText(`refund-${testInfo.project.name}.txt`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "进入知识萃取" }).click();
  await page.getByRole("button", { name: "启动知识萃取" }).click();
  await expect(page.getByRole("heading", { name: "知识研判文档" })).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: testInfo.outputPath("exploration-to-document.png"), fullPage: true });
  expect(consoleErrors).toEqual([]);
});

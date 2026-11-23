import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
test("提交、播放、下载、刷新、素材预览、取消和独立删除", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByText("推理引擎在线")).toBeVisible();
  await page.getByLabel("场景提示词").fill("浏览器验收：山间长镜头");
  await page.getByRole("button", { name: "生成视频", exact: true }).click();
  const player = page.locator(".viewer video");
  await expect(player).toBeVisible({ timeout: 20000 });
  await expect
    .poll(() => player.evaluate((el: HTMLVideoElement) => el.videoWidth))
    .toBe(640);
  await player.evaluate((el: HTMLVideoElement) => {
    el.muted = true;
    return el.play();
  });
  await expect
    .poll(() => player.evaluate((el: HTMLVideoElement) => el.currentTime))
    .toBeGreaterThan(0);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载视频" }).click();
  const saved = await download;
  expect(saved.suggestedFilename()).toMatch(/\.mp4$/);
  const mp4 = await readFile((await saved.path())!);
  await page.reload();
  await expect(page.locator(".inspector .jobprompt")).toHaveText(
    "浏览器验收：山间长镜头",
  );
  await page.getByRole("button", { name: "图生视频", exact: true }).click();
  const png = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 32;
    c.height = 20;
    return c.toDataURL().split(",")[1];
  });
  await page.getByLabel("首帧图片", { exact: true }).setInputFiles({
    name: "start.png",
    mimeType: "image/png",
    buffer: Buffer.from(png, "base64"),
  });
  await expect(page.getByAltText("start.png")).toBeVisible();
  await page.getByLabel("场景提示词").fill("首帧测试");
  await page.getByRole("button", { name: "生成视频", exact: true }).click();
  await expect(page.locator(".inspector .jobprompt")).toHaveText("首帧测试");
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(page.locator(".inspector .pill")).toHaveText("已取消", {
    timeout: 12000,
  });
  await page
    .locator("article.selected")
    .getByRole("button", { name: "删除任务" })
    .click();
  await page
    .locator("article.selected")
    .getByRole("button", { name: "删除", exact: true })
    .click();
  await expect(page.locator(".history article")).toHaveCount(1);
  await page.getByRole("button", { name: "参考生成", exact: true }).click();
  await page.getByLabel("参考视频", { exact: true }).setInputFiles({
    name: "reference.mp4",
    mimeType: "video/mp4",
    buffer: mp4,
  });
  await expect
    .poll(() =>
      page
        .locator(".asset video")
        .evaluate((v: HTMLVideoElement) => v.videoWidth),
    )
    .toBe(640);
  await page.getByLabel("场景提示词").fill("参考视频测试");
  await page.getByRole("button", { name: "生成视频", exact: true }).click();
  await expect(page.locator(".inspector .jobprompt")).toHaveText(
    "参考视频测试",
  );
  await expect(page.locator(".inspector .pill")).toHaveText("已完成", {
    timeout: 15000,
  });
  await page.getByRole("button", { name: "重新生成" }).click();
  await expect(page.locator(".history article")).toHaveCount(3);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

test("Vite 开发代理保留浏览器同源信息", async ({ page }) => {
  await page.goto("http://127.0.0.1:15173/");
  await expect(page.getByText("推理引擎在线")).toBeVisible();
  const lora = page.getByRole("combobox", { name: "LoRA", exact: true });
  await expect(lora).toHaveValue("");
  await expect(
    lora.locator("option[value=hmnsfw_extreme_realism]"),
  ).toBeDisabled();
  await page
    .getByLabel("Checkpoint profile")
    .selectOption("h3_fl2va_int8_convrot");
  await lora.selectOption("hmnsfw_extreme_realism");
  await expect(page.getByLabel("LoRA 强度", { exact: true })).toHaveValue(
    "0.8",
  );
  await expect(page.getByLabel("推理步数")).toHaveValue("8");
  await expect(
    page.getByRole("combobox", { name: "Scheduler", exact: true }),
  ).toHaveValue("simple");
  await page.getByRole("button", { name: "应用推荐参数" }).click();
  await expect(page.getByLabel("推理步数")).toHaveValue("18");
  await expect(
    page.getByRole("combobox", { name: "Scheduler", exact: true }),
  ).toHaveValue("beta");
  await expect(
    page.getByRole("combobox", { name: "Shift", exact: true }),
  ).toHaveValue("12");
  await page.getByLabel("场景提示词").fill("开发页提交验收");
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/api/jobs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成视频", exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const submitted = await response.json();
  expect(submitted.parameters).toMatchObject({
    lora_id: "hmnsfw_extreme_realism",
    lora_strength: 0.8,
    model_profile_id: "h3_fl2va_int8_convrot",
    steps: 18,
    turbo_enabled: false,
  });
  await expect(page.locator(".inspector .jobprompt")).toHaveText(
    "开发页提交验收",
  );
  await expect(page.locator(".inspector .pill")).toHaveText("已完成", {
    timeout: 15000,
  });
  await page.reload();
  await expect(page.locator(".inspector .jobmeta")).toContainText(
    "hmnsfw_extreme_realism",
  );
  await expect
    .poll(() =>
      page
        .locator(".viewer video")
        .evaluate((v: HTMLVideoElement) => v.videoWidth),
    )
    .toBe(640);
});

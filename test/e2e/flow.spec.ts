import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { inspectWebM, measuredDurationMs } from "../../src/webm";

interface AppState {
  status: string;
  capabilities: {
    supported: boolean;
    missing: string[];
  } | null;
  metrics: {
    processed: number;
    droppedBacklog: number;
    queueLength: number;
    maxQueue: number;
    timestampMatches: boolean;
    lastOutputWidth: number;
    lastOutputHeight: number;
  } | null;
  stopReport: {
    framesClosed: number;
    readerCancelled: boolean;
    writerClosed: boolean;
  } | null;
  trackCleanup: {
    allEnded: boolean;
  } | null;
  validation: {
    ok: boolean;
    durationMs: number;
    checks: Array<{ id: string; passed: boolean; details: string[] }>;
  } | null;
  chunks: Array<{ size: number; type: string }>;
  recovered: {
    session: {
      status: string;
      activeDurationMs: number;
      mimeType: string;
    };
    size: number;
  } | null;
}

async function openTestPage(page: Page): Promise<void> {
  await page.goto("/?test=1");
  await page.waitForFunction(() => window.__recorderTestApi !== undefined);
  await page.waitForFunction(
    () => window.__recorderTestApi?.getState() !== undefined,
  );
  await page.waitForFunction(
    () =>
      (window.__recorderTestApi?.getState() as { capabilities: unknown }).capabilities !==
      null,
  );
}

async function state(page: Page): Promise<AppState> {
  return page.evaluate(
    () => window.__recorderTestApi?.getState() as AppState,
  );
}

test("simulated source passes pipeline, cleanup and media validation", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await openTestPage(page);

  await page.locator("#run-selftest").click();
  const result = page.locator("#selftest-result");
  await expect(result).toHaveAttribute("data-status", "passed", { timeout: 30_000 });

  const current = await state(page);
  expect(current.metrics?.processed).toBeGreaterThan(5);
  expect(current.metrics?.queueLength).toBeLessThanOrEqual(
    current.metrics?.maxQueue ?? 2,
  );
  expect(current.metrics?.droppedBacklog).toBeGreaterThan(0);
  expect(current.metrics?.timestampMatches).toBe(true);
  expect(current.metrics?.lastOutputWidth).toBe(384);
  expect(current.metrics?.lastOutputHeight).toBe(216);
  expect(current.stopReport?.framesClosed).toBeGreaterThan(0);
  expect(current.stopReport?.readerCancelled).toBe(true);
  expect(current.stopReport?.writerClosed).toBe(true);
  expect(current.trackCleanup?.allEnded).toBe(true);
  expect(current.validation?.ok).toBe(true);
  expect(current.validation?.durationMs).toBeGreaterThan(1_000);
  expect(current.validation?.checks.every((check) => check.passed)).toBe(true);
  expect(pageErrors).toEqual([]);
  await expect(result).toContainText("文件通过画面与水印检查");
});

test("chunks restore after reload and download is gated by validation", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await openTestPage(page);

  await page.evaluate(() => window.__recorderTestApi?.startSynthetic());
  await page.waitForFunction(
    () =>
      (window.__recorderTestApi?.getState() as { status: string }).status ===
      "recording",
  );
  await page.waitForFunction(
    () =>
      (window.__recorderTestApi?.getState() as { chunks: unknown[] }).chunks.length >=
      4,
    undefined,
    { timeout: 10_000 },
  );

  await page.reload();
  await page.waitForFunction(() => {
    const api = window.__recorderTestApi;
    if (!api) return false;
    const current = api.getState() as { recovered: unknown };
    return Boolean(current.recovered);
  });
  const restored = await state(page);
  expect(restored.recovered?.session.status).toBe("interrupted");
  expect(restored.recovered?.session.activeDurationMs).toBeGreaterThan(300);
  expect(restored.recovered?.size).toBeGreaterThan(1_000);
  await expect(page.locator("#recovery-content")).toContainText("页面刷新或关闭时录制被中断");

  const downloadPromise = page.waitForEvent("download", { timeout: 25_000 });
  await page.locator('[data-testid="validate-download"]').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^timestamp-recording-.*\.webm$/);
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  if (!downloadPath) throw new Error("下载文件没有落盘。");
  const bytes = readFileSync(downloadPath);
  expect(bytes.byteLength).toBeGreaterThan(1_000);
  const buffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  );
  const downloadedFile = inspectWebM(buffer);
  expect(measuredDurationMs(downloadedFile)).toBeGreaterThan(300);
  expect(downloadedFile.video?.codecId).toBe("V_VP8");
  expect(downloadedFile.audio?.channels).toBe(2);

  const validated = await state(page);
  expect(validated.validation?.ok).toBe(true);
  const audio = validated.validation?.checks.find((check) => check.id === "audio");
  const video = validated.validation?.checks.find((check) => check.id === "video");
  expect(audio?.passed).toBe(true);
  expect(audio?.details.join(" ")).toContain("实际声道：2");
  expect(video?.passed).toBe(true);
  expect(video?.details.join(" ")).toContain("左下角水印可辨识样本");
  expect(pageErrors).toEqual([]);
});

test("shows an explicit message when Worker APIs are unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "Worker", {
      configurable: true,
      value: undefined,
    });
  });
  await page.goto("/?test=1");
  await expect(page.locator("#capability-badge")).toHaveText("接口不可用");
  await expect(page.locator("#alert-panel")).toContainText("不支持 Web Worker");
  await expect(page.locator("#start-camera")).toBeDisabled();
  await expect(page.locator("#run-selftest")).toBeDisabled();
});

test("shows a clear permission-denied message", async ({ page }) => {
  await page.addInitScript(() => {
    if (!navigator.mediaDevices) return;
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      configurable: true,
      value: () =>
        Promise.reject(new DOMException("Permission denied by test", "NotAllowedError")),
    });
  });
  await openTestPage(page);
  await page.locator("#start-camera").click();
  await expect(page.locator("#alert-panel")).toContainText("摄像头或麦克风权限被拒绝");
  await expect(page.locator("#record-state")).toHaveText("未录制");
});

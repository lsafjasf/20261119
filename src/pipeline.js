import { VideoFrameOwnershipTracker } from "./tracker.js";
import { SimulatedVideoFrame, createMonotonicClock } from "./simulated-frame.js";

function trackScenario(scenarioId, prefix) {
  return new VideoFrameOwnershipTracker({
    scenarioId,
    idPrefix: prefix,
    clock: createMonotonicClock(),
  });
}

function runNormalPath() {
  const tracker = trackScenario("normal", "VF-N");
  const source = new SimulatedVideoFrame({ label: "camera-1080p" });
  const sourceId = tracker.create(source, {
    stage: "decode:create",
    owner: "decoder-worker",
    label: "camera source",
  });
  tracker.markStage("decode:create", { note: "解码 Worker 创建源帧" });

  const preview = source.clone();
  tracker.clone(source, preview, {
    stage: "fanout:clone",
    owner: "decoder-worker",
    label: "preview clone",
  });
  tracker.use(source, { stage: "fanout:source-use", operation: "decode" });
  tracker.markStage("fanout:clone", { note: "克隆预览帧，源帧与克隆同时存活" });

  tracker.transferOut(source, {
    from: "decoder-worker",
    to: "render-worker",
    stage: "transfer:posted",
    channel: "dedicated-worker",
  });
  tracker.markStage("transfer:posted", { note: "源帧在 Worker 通道中，所有权尚未落地" });

  tracker.acceptTransfer(sourceId, source, {
    stage: "transfer:accepted",
    channel: "dedicated-worker",
  });
  tracker.markStage("transfer:accepted", { note: "render-worker 接管源帧" });

  tracker.use(preview, { stage: "render:preview-use", operation: "preview" });
  tracker.close(preview, { stage: "render:preview-close", reason: "preview-consumed" });
  tracker.markStage("render:preview-close", { note: "预览帧已释放" });

  tracker.use(source, { stage: "render:source-use", operation: "render" });
  tracker.close(source, { stage: "render:source-close", reason: "render-complete" });
  tracker.markStage("render:source-close", { note: "源帧已释放" });

  tracker.markStage("cleanup:verify", { note: "正常路径闭环，未发现存活帧" });
  return tracker.exportScenario({
    title: "正常处理",
    description: "解码、克隆、跨 Worker 转移、渲染并按顺序关闭。",
    outcome: "clean",
    metadata: { path: "normal", expectedResidual: 0 },
  });
}

function runCancellationPath() {
  const tracker = trackScenario("cancelled", "VF-C");
  const source = new SimulatedVideoFrame({ label: "cancelled-source", timestamp: 40 });
  tracker.create(source, {
    stage: "decode:create",
    owner: "decoder-worker",
    label: "source",
  });

  const compositorCopy = source.clone();
  tracker.clone(source, compositorCopy, {
    stage: "fanout:clone-compositor",
    owner: "decoder-worker",
    label: "compositor copy",
  });
  const encoderCopy = source.clone();
  tracker.clone(source, encoderCopy, {
    stage: "fanout:clone-encoder",
    owner: "decoder-worker",
    label: "encoder copy",
  });
  tracker.markStage("fanout:clone", { note: "一源两副本，共三帧" });

  tracker.use(encoderCopy, { stage: "fanout:encoder-use", operation: "encode" });
  tracker.close(encoderCopy, { stage: "cancel:drop-local", reason: "not-selected" });

  tracker.transferOut(source, {
    from: "decoder-worker",
    to: "render-worker",
    stage: "cancel:transfer-source",
    channel: "dedicated-worker",
  });
  tracker.transferOut(compositorCopy, {
    from: "decoder-worker",
    to: "compositor-worker",
    stage: "cancel:transfer-compositor",
    channel: "compositor-worker",
  });
  tracker.markStage("cancel:in-flight", {
    note: "取消信号到达时，两帧仍在跨 Worker 传输",
  });
  tracker.inspectUnreleased({
    stage: "cancel:in-flight",
    reason: "pipeline cancelled before transfer completion",
  });

  tracker.close(source, { stage: "cancel:drain-source", reason: "cancelled-transfer" });
  tracker.close(compositorCopy, {
    stage: "cancel:drain-compositor",
    reason: "cancelled-transfer",
  });
  tracker.markStage("cancel:drain", { note: "取消排空完成，在途帧由取消路径释放" });
  tracker.markStage("cleanup:verify", { note: "取消后残留帧为零" });

  return tracker.exportScenario({
    title: "取消时仍有在途帧",
    description: "取消发生在两个 postMessage 转移完成之前，告警后执行排空清理。",
    outcome: "cancelled-and-drained",
    metadata: {
      path: "cancelled",
      expectedResidualAtCancel: 2,
      expectedFinalResidual: 0,
    },
  });
}

function runAbnormalExitPath() {
  const tracker = trackScenario("abnormal", "VF-A");
  const source = new SimulatedVideoFrame({ label: "gpu-source", timestamp: 80 });
  const sourceId = tracker.create(source, {
    stage: "decode:create",
    owner: "decoder-worker",
    label: "GPU upload source",
  });
  const localCopy = source.clone();
  tracker.clone(source, localCopy, {
    stage: "fanout:clone",
    owner: "decoder-worker",
    label: "local diagnostics copy",
  });
  tracker.markStage("fanout:clone", { note: "源帧与诊断副本同时存活" });

  tracker.transferOut(source, {
    from: "decoder-worker",
    to: "gpu-worker",
    stage: "transfer:posted",
    channel: "gpu-worker",
  });
  tracker.acceptTransfer(sourceId, source, {
    stage: "transfer:accepted",
    channel: "gpu-worker",
  });
  tracker.markStage("transfer:accepted", { note: "GPU Worker 接管源帧" });

  tracker.close(localCopy, { stage: "render:local-close", reason: "diagnostics-complete" });
  tracker.markStage("render:local-close", { note: "本地副本释放，GPU 侧仍存活一帧" });

  let crashEvidence = null;
  try {
    throw new Error("gpu-worker exited with code 139");
  } catch (error) {
    crashEvidence = {
      name: error.name,
      message: error.message,
      stage: "worker:crash",
    };
  }
  tracker.markStage("worker:crash", { note: "GPU Worker 异常退出，源帧成为孤儿" });
  tracker.inspectUnreleased({
    stage: "worker:crash",
    reason: "owner worker exited before releasing the frame",
  });

  tracker.closeById(sourceId, {
    stage: "recovery:close-orphan",
    reason: "weak-reference recovery close",
  });
  tracker.markStage("recovery:close-orphan", { note: "恢复流程关闭孤儿帧" });
  tracker.markStage("cleanup:verify", { note: "异常退出路径清理完成" });

  return tracker.exportScenario({
    title: "异常退出",
    description: "GPU Worker 崩溃后留下孤儿帧，追踪器定位后由恢复流程关闭。",
    outcome: "recovered-after-crash",
    metadata: {
      path: "abnormal-exit",
      crash: crashEvidence,
      expectedResidualAtCrash: 1,
      expectedFinalResidual: 0,
    },
  });
}

function runFaultInjectionPath() {
  const tracker = trackScenario("fault-injection", "VF-X");
  const faultFrame = new SimulatedVideoFrame({ label: "fault-target", timestamp: 120 });
  tracker.create(faultFrame, {
    stage: "fault:setup",
    owner: "render-worker",
    label: "double-close/use-after-close target",
  });
  tracker.close(faultFrame, { stage: "fault:valid-close", reason: "fault-setup" });
  tracker.markStage("fault:valid-close", { note: "目标帧正常关闭" });

  tracker.close(faultFrame, {
    stage: "fault:double-close",
    reason: "injected duplicate cleanup",
  });
  tracker.markStage("fault:double-close", { note: "注入第二次 close" });

  tracker.use(faultFrame, {
    stage: "fault:use-after-close",
    operation: "injected-render",
  });
  tracker.markStage("fault:use-after-close", { note: "注入关闭后使用" });

  const leakedFrame = new SimulatedVideoFrame({ label: "unreleased", timestamp: 160 });
  tracker.create(leakedFrame, {
    stage: "fault:create-unreleased",
    owner: "encoder-worker",
    label: "intentionally unreleased frame",
  });
  tracker.markStage("fault:unreleased", { note: "故意不关闭，验证未释放告警" });
  tracker.inspectUnreleased({
    stage: "fault:unreleased",
    reason: "injected missing close",
  });

  tracker.close(leakedFrame, {
    stage: "fault:cleanup-unreleased",
    reason: "fault-test-teardown",
  });
  tracker.markStage("fault:cleanup-unreleased", { note: "验证后关闭注入的泄漏帧" });
  tracker.markStage("cleanup:verify", { note: "故障注入场景清理完成" });

  return tracker.exportScenario({
    title: "主动故障注入验证",
    description: "注入重复关闭、关闭后使用和遗漏关闭，校验告警代码与阶段位置。",
    outcome: "faults-detected-and-cleaned",
    metadata: {
      path: "fault-injection",
      expectedDiagnostics: [
        { code: "double-close", stage: "fault:double-close" },
        { code: "use-after-close", stage: "fault:use-after-close" },
        { code: "unreleased-frame", stage: "fault:unreleased" },
      ],
    },
  });
}

export function runDemo() {
  const scenarios = [
    runNormalPath(),
    runCancellationPath(),
    runAbnormalExitPath(),
    runFaultInjectionPath(),
  ];
  const pipelineScenarios = scenarios.slice(0, 3);
  return {
    generatedAt: new Date().toISOString(),
    scenarios,
    summary: {
      pipelinePaths: pipelineScenarios.length,
      scenarios: scenarios.length,
      framesCreated: scenarios.reduce((sum, item) => sum + item.summary.frameCount, 0),
      framesClosed: scenarios.reduce((sum, item) => sum + item.summary.closedFrameCount, 0),
      residualFrames: scenarios.reduce((sum, item) => sum + item.summary.openFrameCount, 0),
      diagnostics: scenarios.reduce((sum, item) => sum + item.summary.diagnosticCount, 0),
      pipelineResidualFrames: pipelineScenarios.reduce(
        (sum, item) => sum + item.summary.openFrameCount,
        0,
      ),
      allClean: scenarios.every((item) => item.cleanup.clean),
    },
  };
}

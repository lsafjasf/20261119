// worker-port.js — 让同一 Worker 文件同时跑在浏览器 DedicatedWorker 与
// Node worker_threads 中：统一为 { postMessage(msg), onMessage(fn) } 接口。
export async function getPort() {
  if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
    return {
      postMessage: (msg) => self.postMessage(msg),
      onMessage: (fn) => { self.onmessage = (e) => fn(e.data); },
    };
  }
  const { parentPort } = await import('node:worker_threads');
  return {
    postMessage: (msg) => parentPort.postMessage(msg),
    onMessage: (fn) => parentPort.on('message', fn),
  };
}

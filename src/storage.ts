import type { RecordingSession, StoredChunk } from "./types";

const DB_NAME = "timestamp-camera-recorder";
const DB_VERSION = 1;
const SESSION_STORE = "sessions";
const CHUNK_STORE = "chunks";

interface StoredRecording {
  session: RecordingSession;
  chunks: StoredChunk[];
  blob: Blob;
}

let databasePromise: Promise<IDBDatabase> | null = null;

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB 请求失败。"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () =>
      reject(transaction.error ?? new Error("IndexedDB 事务失败。"));
    transaction.onabort = () =>
      reject(transaction.error ?? new Error("IndexedDB 事务已中止。"));
  });
}

function openDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("当前浏览器不支持 IndexedDB，无法保存或恢复录制。"));
  }
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(SESSION_STORE)) {
        const sessions = database.createObjectStore(SESSION_STORE, { keyPath: "id" });
        sessions.createIndex("createdAt", "createdAt");
      }
      if (!database.objectStoreNames.contains(CHUNK_STORE)) {
        const chunks = database.createObjectStore(CHUNK_STORE, { keyPath: "key" });
        chunks.createIndex("sessionId", "sessionId");
        chunks.createIndex("sessionSequence", ["sessionId", "sequence"]);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new Error("无法打开录制数据库。"));
    request.onblocked = () => reject(new Error("录制数据库被其他页面占用，请关闭旧页面后重试。"));
  });
  return databasePromise;
}

export function newSession(
  sourceKind: RecordingSession["sourceKind"],
  mimeType: string,
  expectedChannels: number,
  resolution: string,
): RecordingSession {
  const now = Date.now();
  return {
    id: globalThis.crypto?.randomUUID?.() ?? `session-${now}-${Math.random()}`,
    createdAt: now,
    updatedAt: now,
    status: "recording",
    mimeType,
    expectedChannels,
    activeDurationMs: 0,
    chunkCount: 0,
    sourceKind,
    resolution,
  };
}

export async function saveSession(session: RecordingSession): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(SESSION_STORE, "readwrite");
  transaction.objectStore(SESSION_STORE).put({ ...session, updatedAt: Date.now() });
  await transactionDone(transaction);
}

export async function appendChunk(
  session: RecordingSession,
  sequence: number,
  blob: Blob,
): Promise<void> {
  if (blob.size === 0) return;
  const database = await openDatabase();
  const transaction = database.transaction([SESSION_STORE, CHUNK_STORE], "readwrite");
  const chunk: StoredChunk = {
    key: `${session.id}:${String(sequence).padStart(8, "0")}`,
    sessionId: session.id,
    sequence,
    blob,
    createdAt: Date.now(),
  };
  transaction.objectStore(CHUNK_STORE).put(chunk);
  transaction.objectStore(SESSION_STORE).put({
    ...session,
    chunkCount: Math.max(session.chunkCount, sequence + 1),
    updatedAt: Date.now(),
  });
  await transactionDone(transaction);
}

export async function getChunks(sessionId: string): Promise<StoredChunk[]> {
  const database = await openDatabase();
  const transaction = database.transaction(CHUNK_STORE, "readonly");
  const index = transaction.objectStore(CHUNK_STORE).index("sessionId");
  const chunks = await requestResult(index.getAll(IDBKeyRange.only(sessionId)));
  await transactionDone(transaction);
  return chunks.sort((left, right) => left.sequence - right.sequence);
}

export async function getLatestSession(): Promise<RecordingSession | null> {
  const database = await openDatabase();
  const transaction = database.transaction(SESSION_STORE, "readonly");
  const sessions = await requestResult(
    transaction.objectStore(SESSION_STORE).getAll(),
  );
  await transactionDone(transaction);
  sessions.sort((left, right) => right.createdAt - left.createdAt);
  return sessions[0] ?? null;
}

export async function loadLatestRecording(): Promise<StoredRecording | null> {
  const session = await getLatestSession();
  if (!session) return null;
  const chunks = await getChunks(session.id);
  if (chunks.length === 0) return null;
  if (session.status === "recording" || session.status === "paused") {
    session.status = "interrupted";
    await saveSession(session);
  }
  return {
    session,
    chunks,
    blob: new Blob(chunks.map((chunk) => chunk.blob), { type: session.mimeType }),
  };
}

export async function deleteRecording(sessionId: string): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction([SESSION_STORE, CHUNK_STORE], "readwrite");
  transaction.objectStore(SESSION_STORE).delete(sessionId);
  const index = transaction.objectStore(CHUNK_STORE).index("sessionId");
  const cursorRequest = index.openKeyCursor(IDBKeyRange.only(sessionId));
  await new Promise<void>((resolve, reject) => {
    cursorRequest.onerror = () => reject(cursorRequest.error ?? new Error("读取录制片段失败。"));
    cursorRequest.onsuccess = () => {
      const cursor = cursorRequest.result;
      if (!cursor) {
        resolve();
        return;
      }
      transaction.objectStore(CHUNK_STORE).delete(cursor.primaryKey);
      cursor.continue();
    };
  });
  await transactionDone(transaction);
}

export async function canPersistRecordings(): Promise<boolean> {
  try {
    await openDatabase();
    return true;
  } catch {
    return false;
  }
}

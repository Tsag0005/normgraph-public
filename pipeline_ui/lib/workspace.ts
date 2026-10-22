// Browser-owned workspace persistence and transfer to the compute worker.

const DB_NAME = 'normgraph-workspace';
const STORE = 'workspace';
const API_BASE =
  import.meta.env.VITE_PIPELINE_API ??
  (typeof window !== 'undefined' && window.location.port === '3000'
    ? 'http://localhost:8765'
    : '');

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE))
        request.result.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function get<T>(key: string): Promise<T | undefined> {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, 'readonly');
      const request = transaction.objectStore(STORE).get(key);
      request.onsuccess = () => resolve(request.result as T | undefined);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

async function put(key: string, value: unknown): Promise<void> {
  const db = await database();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE, 'readwrite');
      transaction.objectStore(STORE).put(value, key);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  const value = await response.json().catch(() => ({}));
  throw new Error(value?.error || `Workspace request failed (${response.status})`);
}

let opening: Promise<void> | undefined;
let needsRestore = false;
let reopening: Promise<void> | undefined;

export function reopenWorkspace(): Promise<void> {
  if (reopening) return reopening;
  opening = undefined;
  reopening = ensureWorkspace().finally(() => { reopening = undefined; });
  return reopening;
}

export function ensureWorkspace(): Promise<void> {
  if (opening) return opening;
  opening = (async () => {
    let token = await get<string>('token');
    if (!token) {
      token = Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) =>
        byte.toString(16).padStart(2, '0'),
      ).join('');
      await put('token', token);
    }
    const response = await checked(
      await fetch(`${API_BASE}/api/workspace/open`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      }),
    );
    const { hasData } = (await response.json()) as { hasData: boolean };
    if (!hasData || needsRestore) {
      const snapshot = await get<Blob>('snapshot');
      if (snapshot) {
        needsRestore = true;
        await uploadSnapshot(snapshot);
        needsRestore = false;
      }
    }
  })().catch((error) => {
    opening = undefined;
    throw error;
  });
  return opening;
}

async function uploadSnapshot(snapshot: Blob): Promise<void> {
  await checked(
    await fetch(`${API_BASE}/api/workspace/snapshot`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/zip' },
      body: snapshot,
    }),
  );
}

let syncing: Promise<void> | undefined;

export function syncWorkspace(): Promise<void> {
  if (syncing) return syncing;
  syncing = (async () => {
    await ensureWorkspace();
    let response = await fetch(`${API_BASE}/api/workspace/snapshot`, {
        credentials: 'include',
        cache: 'no-store',
      });
    if (response.status === 409) {
      await reopenWorkspace();
      response = await fetch(`${API_BASE}/api/workspace/snapshot`, {
        credentials: 'include', cache: 'no-store',
      });
    }
    await checked(response);
    await put('snapshot', await response.blob());
  })().finally(() => {
    syncing = undefined;
  });
  return syncing;
}

export async function importWorkspace(file: File): Promise<void> {
  if (file.size > 30 * 1024 * 1024) throw new Error('Workspace archive exceeds 30 MB');
  await ensureWorkspace();
  await uploadSnapshot(file);
  await put('snapshot', file);
}

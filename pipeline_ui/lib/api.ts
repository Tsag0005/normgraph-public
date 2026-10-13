import { ensureWorkspace, reopenWorkspace } from './workspace';

export const API_BASE =
  import.meta.env.VITE_PIPELINE_API ??
  (typeof window !== 'undefined' && window.location.port === '3000'
    ? 'http://localhost:8765'
    : '');

let credentials = { openai: '', uri: '', username: '', password: '', database: '' };

export function setCredentials(value: typeof credentials): void {
  credentials = value;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  await ensureWorkspace();
  const headers = new Headers(init?.headers);
  headers.set('Content-Type', 'application/json');
  if (init?.method === 'POST' && /^\/api\/jobs\/[^/]+$/.test(path)) {
    if (credentials.openai) headers.set('X-OpenAI-Key', credentials.openai);
    if (credentials.uri) headers.set('X-Neo4j-URI', credentials.uri);
    if (credentials.username) headers.set('X-Neo4j-Username', credentials.username);
    if (credentials.password) headers.set('X-Neo4j-Password', credentials.password);
    if (credentials.database) headers.set('X-Neo4j-Database', credentials.database);
  }
  let response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });
  if (response.status === 409) {
    await reopenWorkspace();
    response = await fetch(`${API_BASE}${path}`, {
      ...init, headers, credentials: 'include',
    });
  }
  const payload = await response.json();
  if (!response.ok)
    throw new Error(
      typeof payload === 'object' && payload && 'error' in payload
        ? String(payload.error)
        : `Request failed (${response.status})`,
    );
  return payload as T;
}

export type Corpus = {
  id: string;
  name: string;
  description: string;
  nodePrefix: string;
  filePrefix: string;
  profile: string;
  profileExists: boolean;
  sourceExists: boolean;
  builtIn: boolean;
  artifacts: { raw: number; clean: number; verified: number };
  buildStatus: string;
  errors: string[];
};
export type ContextDetails = {
  target_provision?: string;
  norms_included?: number;
  provisions_included?: number;
  exceptions_included?: number;
  token_estimate?: number;
  inbound_provisions?: string[];
  outbound_provisions?: string[];
};
export type Run = {
  provision?: string;
  text?: string;
  queryContext?: string;
  feature?: string;
  prompt?: string;
  summary?: string;
  error?: string;
  warnings?: string[];
  details?: ContextDetails;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    usage_source?: string;
    [key: string]: unknown;
  };
  record?: Record<string, unknown>;
};
export type Job = {
  id: string;
  kind: string;
  label: string;
  status: string;
  createdAt: number;
  finishedAt?: number;
  stage?: string;
  progress?: { done: number; total: number };
  logs: string[];
  persistenceWarning?: string;
  error?: string;
  request: Record<string, unknown>;
  command?: string;
  result?: {
    summary?: string;
    error?: string;
    warnings?: string[];
    provisions?: string[] | number;
    profile?: string;
    verification?: { provision: string; status: string; message: string }[];
    files?: string[];
    items?: Run[];
    runs?: Run[];
  };
};

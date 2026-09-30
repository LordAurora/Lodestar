// Typed client for the Lodestar backend. Everything goes to the same origin
// (/api), which Vite proxies to FastAPI in development.

export interface Repo {
  id: string;
  path: string;
  name: string;
  created_at: number;
  last_indexed_at: number | null;
  file_count: number;
  chunk_count: number;
  embedding_model: string | null;
  indexing: boolean;
  last_error: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
}

export interface Source {
  id: number;
  chunk_id: string;
  file_path: string;
  language: string;
  symbol_name: string | null;
  symbol_kind: string;
  start_line: number;
  end_line: number;
  content: string;
  score: number;
  cosine: number;
}

export interface Citation {
  n: number;
  chunk_id: string;
}

export interface MessageMeta {
  sources?: Source[];
  citations?: Citation[];
  query?: string;
  no_answer?: boolean;
  suggestions?: Source[];
}

export interface Message {
  id: number;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  meta: MessageMeta;
  created_at: number;
}

export interface Health {
  status: string;
  foundry: { running: boolean; endpoint: string | null; error: string | null; gpu: boolean };
  chat_model: { alias: string; ready: boolean; device: "GPU" | "CPU" | null };
  embedder: { name: string | null; loaded: boolean; error: string | null };
  privacy: {
    guard_enabled: boolean;
    blocking: boolean;
    external_attempts: number;
    last_blocked: string[];
    local_connections: number;
    local_only: boolean;
  };
}

export interface ModelJob {
  alias: string;
  state: "downloading" | "loading" | "ready" | "error";
  progress: number;
  error: string | null;
}

export interface ModelInfo {
  alias: string;
  id: string;
  display_name: string;
  size_mb: number | null;
  cached: boolean;
  loaded: boolean;
  device: "GPU" | "CPU";
  job: ModelJob | null;
}

export interface Settings {
  chat_model: string;
  top_k: number;
  relevance_threshold: number;
  hybrid_search: boolean;
  embedding_model: string;
}

export interface IndexProgress {
  status: "pending" | "scanning" | "embedding" | "analyzing" | "done" | "error";
  files_total: number;
  files_scanned: number;
  files_changed: number;
  files_deleted: number;
  chunks_created: number;
  chunks_embedded: number;
  analysis_total: number;
  analysis_done: number;
  analysis_error: string | null;
  elapsed: number;
  error: string | null;
  recent_files: string[];
  repo?: Repo;
}

export interface ChunkPreview {
  // Chunk previews carry every field of a Source; file previews only the location.
  chunk: Pick<Source, "file_path" | "start_line" | "end_line"> &
    Partial<Omit<Source, "file_path" | "start_line" | "end_line">>;
  language: string;
  first_line: number;
  lines: string[];
  stale: boolean;
  absolute_path: string;
}

// ---- Insights ---------------------------------------------------------------

export type Confidence = "high" | "medium" | "low";

export interface InsightsStatus {
  analyzed: boolean;
  last_analyzed_at: number | null;
  analyzers: Record<string, number>;
  counts: {
    symbols: number;
    tests: number;
    undocumented: number;
    env_vars: number;
    debt_items: number;
    call_edges: number;
    imports: number;
    [key: string]: number;
  };
}

export interface EnvUsage {
  file_path: string;
  line: number;
  language: string;
  required: boolean;
  has_default: boolean;
  default: string | null;
  source: "code" | "pydantic";
  symbol: string | null;
}

export interface EnvVariable {
  name: string;
  is_secret: boolean;
  required: boolean;
  default: string | null;
  default_varies: boolean;
  usage_count: number;
  declared_in: string[];
  usages: EnvUsage[];
}

export interface DebtItem {
  id: number;
  tag: string;
  text: string;
  file_path: string;
  line: number;
  symbol: string | null;
  author: string | null;
  assignee: string | null;
  commit_date: number | null;
  age_days: number | null;
}

export interface DebtGroup {
  key: string;
  label: string;
  items: DebtItem[];
}

export interface SymbolHit {
  id: number;
  name: string;
  qualified_name: string;
  kind: "function" | "method" | "class";
  file_path: string;
  line: number;
  is_test: boolean;
  callers: number;
}

export interface ImpactItem {
  id: number;
  name: string;
  qualified_name: string;
  kind: "function" | "method" | "class";
  file_path: string;
  line: number;
  call_file: string;
  call_line: number;
  confidence: Confidence;
  is_test: boolean;
  depth: number;
}

export interface ImpactReport {
  target: {
    id: number;
    name: string;
    qualified_name: string;
    kind: string;
    file_path: string;
    line: number;
    end_line: number;
  };
  depth: number;
  levels: { depth: number; items: ImpactItem[] }[];
  tests: ImpactItem[];
  module_level: { file_path: string; line: number; confidence: Confidence }[];
  total: number;
  direct: number;
  files: number;
  production_files: number;
  score: number;
  level: "Low" | "Medium" | "High";
  formula: string;
  low_confidence: number;
  possible_unresolved: number;
  unresolved_warning: boolean;
  truncated: boolean;
}

export interface Endpoint {
  id: number;
  method: string;
  path: string;
  handler: string | null;
  handler_symbol_id: number | null;
  symbol: string | null;
  file_path: string;
  line: number;
  framework: string;
  framework_label: string;
  partial: boolean;
}

export interface EndpointList {
  endpoints: Endpoint[];
  total: number;
  shown: number;
  methods: { method: string; count: number }[];
  frameworks: { framework: string; label: string; count: number }[];
}

export interface EndpointFilters {
  method?: string;
  framework?: string;
  q?: string;
}

export type DebtGroupBy = "tag" | "folder" | "topic" | "age";

export interface DebtFilters {
  tag?: string;
  q?: string;
  folder?: string;
  older_than_days?: number;
  group_by?: DebtGroupBy;
}

export interface DebtBoard {
  group_by: DebtGroupBy;
  groups: DebtGroup[];
  shown: number;
  topics_pending: boolean;
  topics_error: string | null;
  counters: {
    total: number;
    by_tag: Record<string, number>;
    oldest: DebtItem | null;
    has_dates: boolean;
  };
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "" && value !== 0) search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Lodestar backend. Is it running on port 8000?");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  health: () => request<Health>("/health"),
  models: () => request<{ selected: string; models: ModelInfo[] }>("/models"),
  loadModel: (alias: string) =>
    request<ModelJob>(`/models/${encodeURIComponent(alias)}/load`, { method: "POST" }),
  settings: () => request<Settings>("/settings"),
  saveSettings: (changes: Partial<Settings>) =>
    request<Settings>("/settings", { method: "PUT", body: json(changes) }),

  repos: () => request<Repo[]>("/repos"),
  addRepo: (path: string) => request<Repo>("/repos", { method: "POST", body: json({ path }) }),
  deleteRepo: (id: string) => request<void>(`/repos/${id}`, { method: "DELETE" }),
  startIndex: (id: string) => request<{ job_id: string }>(`/repos/${id}/index`, { method: "POST" }),

  conversations: (repoId: string) => request<Conversation[]>(`/repos/${repoId}/conversations`),
  messages: (repoId: string, convId: string) =>
    request<Message[]>(`/repos/${repoId}/conversations/${convId}/messages`),
  renameConversation: (repoId: string, convId: string, title: string) =>
    request<Conversation>(`/repos/${repoId}/conversations/${convId}`, {
      method: "PATCH",
      body: json({ title }),
    }),
  deleteConversation: (repoId: string, convId: string) =>
    request<void>(`/repos/${repoId}/conversations/${convId}`, { method: "DELETE" }),

  chunk: (chunkId: string) => request<ChunkPreview>(`/chunks/${chunkId}`),
  file: (repoId: string, path: string, start: number, end: number) =>
    request<ChunkPreview>(`/repos/${repoId}/file${query({ path, start, end })}`),

  insightsStatus: (repoId: string) => request<InsightsStatus>(`/repos/${repoId}/insights/status`),
  analyze: (repoId: string) =>
    request<{ job_id: string }>(`/repos/${repoId}/analyze`, { method: "POST" }),
  env: (repoId: string, q = "") =>
    request<{ variables: EnvVariable[]; total: number }>(`/repos/${repoId}/env${query({ q })}`),
  envExample: (repoId: string) =>
    request<{ text: string; count: number }>(`/repos/${repoId}/env/example`),
  symbols: (repoId: string, q: string, kind = "") =>
    request<{ symbols: SymbolHit[] }>(`/repos/${repoId}/symbols${query({ q, kind, limit: 12 })}`),
  impact: (repoId: string, symbolId: number, depth: number) =>
    request<ImpactReport>(`/repos/${repoId}/impact/${symbolId}${query({ depth })}`),
  endpoints: (repoId: string, filters: EndpointFilters) =>
    request<EndpointList>(`/repos/${repoId}/endpoints${query({ ...filters })}`),
  debt: (repoId: string, filters: DebtFilters) =>
    request<DebtBoard>(`/repos/${repoId}/debt${query({ ...filters })}`),
  refreshDebtTopics: (repoId: string) =>
    request<{ status: string }>(`/repos/${repoId}/debt/topics/refresh`, { method: "POST" }),
};

/** A URL that downloads the endpoint catalog (respecting the current filters). */
export function endpointsExportUrl(
  repoId: string,
  format: "json" | "csv" | "markdown",
  filters: EndpointFilters = {},
): string {
  return `/api/repos/${repoId}/endpoints/export${query({ format, ...filters })}`;
}

/** A URL that downloads the tech debt list in the given format. */
export function debtExportUrl(
  repoId: string,
  format: "markdown" | "csv",
  filters: DebtFilters = {},
): string {
  const { group_by: _ignored, ...rest } = filters;
  void _ignored;
  return `/api/repos/${repoId}/debt/export${query({ format, ...rest })}`;
}

/** Follow indexing progress with the browser's EventSource (GET SSE). */
export function watchIndex(
  repoId: string,
  onEvent: (event: "progress" | "done" | "error" | "idle", data: IndexProgress) => void,
): () => void {
  const source = new EventSource(`/api/repos/${repoId}/index/stream`);
  for (const kind of ["progress", "done", "error", "idle"] as const) {
    source.addEventListener(kind, (e) => {
      onEvent(kind, JSON.parse((e as MessageEvent).data));
      if (kind !== "progress") source.close();
    });
  }
  source.onerror = () => source.close();
  return () => source.close();
}

export type ChatEvent =
  | {
      event: "retrieval";
      data: { query: string; chunks: Source[]; best_cosine: number; conversation_id: string };
    }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: { answer: string; citations: Citation[]; message_id: number } }
  | { event: "no_answer"; data: { message: string; best_cosine: number; suggestions: Source[] } }
  | { event: "error"; data: { message: string } };

/**
 * POST and read the Server-Sent Events stream that comes back.
 * EventSource only supports GET, so we parse the stream by hand: events are
 * separated by a blank line, and each has `event:` and `data:` fields.
 */
async function postEventStream<E>(
  path: string,
  body: unknown,
  onEvent: (e: E) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: body === undefined ? undefined : json(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    throw new ApiError(0, "Cannot reach the Lodestar backend. Is it running on port 8000?");
  }
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail ?? res.statusText);
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (data.length) onEvent({ event, data: JSON.parse(data.join("\n")) } as E);
      }
    }
  } catch (err) {
    if ((err as Error).name !== "AbortError") throw err;
  }
}

/** Ask a question about a repository; the answer streams back as chat events. */
export const streamChat = (
  repoId: string,
  body: { question: string; conversation_id?: string | null; regenerate?: boolean },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
) => postEventStream<ChatEvent>(`/repos/${repoId}/chat`, body, onEvent, signal);

export type SummaryEvent =
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: { text: string; fallback: boolean; reason: string | null } };

/** Stream the plain-language risk summary of an impact report. */
export const streamImpactSummary = (
  repoId: string,
  symbolId: number,
  depth: number,
  onEvent: (e: SummaryEvent) => void,
  signal?: AbortSignal,
) =>
  postEventStream<SummaryEvent>(
    `/repos/${repoId}/impact/${symbolId}/summary${query({ depth })}`,
    undefined,
    onEvent,
    signal,
  );

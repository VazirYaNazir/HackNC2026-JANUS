import { normalizeLayout } from '@janus/design';
import { parsePanelDocument, parseServerMessage, type GeneratedDraft, type GenerationRequest, type JobView, type Layout, type PairResponse, type PanelDocument, type ProgramMatch, type ServerMessage, type WidgetValue } from '@janus/schema';

export interface Connection { host: string; token: string }
export type { PanelDocument };
/** `generation_available` is false when the backend has no Gemini credential; absent on older backends. */
export interface Session { device_id: string; online: boolean; generation_available?: boolean; target: { platform: GenerationRequest['platform']; architecture: GenerationRequest['architecture']; build_boundary_available: boolean; execution_boundary_available: boolean } | null }
export interface Dispatch { widget_id: string; job_id: string; value?: WidgetValue; program_id?: string; request_sha256?: string }
export interface Prepared { request: GenerationRequest; request_sha256: string; checked: boolean; matches: ProgramMatch[] }
export interface PairingCode { code: string; expires_in_seconds: number; host?: string; pwa_url?: string | null }

/** An HTTP error from the backend. `status` lets callers tell "not authorized" (401) from other failures. */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function backendHost(value: string): string {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('Enter the backend address without paths or credentials.');
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) throw new Error('Remote backends require HTTPS.');
  return url.origin;
}

/** One minute of a metric from Tiger Data's metrics_1m aggregate. */
export interface MetricPoint { time: string; avg: number; max: number; min: number }
export interface MetricHistory { available: boolean; metric: string; range: string; points: MetricPoint[] }
export interface ActivityEvent {
  time: string;
  source: 'widget' | 'imessage' | 'generated';
  action_id: string;
  args: Record<string, unknown>;
  outcome: string;
  request_id: string | null;
}
export interface Activity { available: boolean; events: ActivityEvent[] }

export class PanelClient {
  readonly host: string;
  readonly token: string;
  constructor(connection: Connection) {
    this.host = backendHost(connection.host);
    this.token = connection.token;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Invalid API path');
    const url = new URL(path, this.host);
    if (url.origin !== this.host) throw new Error('Invalid API path');
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${this.token}`);
    if (init.body !== undefined) headers.set('Content-Type', 'application/json');
    const response = await fetch(url, { ...init, headers, signal: init.signal ?? AbortSignal.timeout(30000) });
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch { /* no JSON response */ }
      throw new ApiError(detail, response.status);
    }
    return response.json() as Promise<T>;
  }

  /** The saved layout. Throws if the server's document is malformed or its geometry is invalid. */
  async layout(signal?: AbortSignal): Promise<PanelDocument> {
    const document = parsePanelDocument(await this.request<unknown>('/api/layout', { signal }));
    try { normalizeLayout(document.layout); }
    catch (reason) { throw new Error(`The saved panel is invalid: ${reason instanceof Error ? reason.message : 'bad layout'}`, { cause: reason }); }
    return document;
  }
  history(signal?: AbortSignal) { return this.request<{ jobs: JobView[] }>('/api/v1/agent-jobs', { signal }); }
  session(signal?: AbortSignal) { return this.request<Session>('/api/v1/panels/session', { signal }); }
  /** `available: false` (and no points) when the backend has no Tiger Data store, or it is unreachable. */
  metricHistory(metric: string, range = '1h', signal?: AbortSignal) {
    return this.request<MetricHistory>(`/api/metrics/history?metric=${encodeURIComponent(metric)}&range=${encodeURIComponent(range)}`, { signal });
  }
  activity(signal?: AbortSignal) { return this.request<Activity>('/api/activity', { signal }); }
  save(layout: Layout, revision: number) { return this.request<PanelDocument>('/api/v1/studio/layout', { method: 'PUT', body: JSON.stringify({ layout, revision }) }); }
  configure(action: string, request: GenerationRequest) { return this.request(`/api/v1/studio/actions/${encodeURIComponent(action)}`, { method: 'PUT', body: JSON.stringify(request) }); }
  /** Generate a source draft only. No laptop commands, local validation, or execution are requested. */
  generate(request: GenerationRequest, signal?: AbortSignal) {
    return this.request<GeneratedDraft>('/api/v1/programs/generate', { method: 'POST', body: JSON.stringify(request), signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) });
  }
  pairing(signal?: AbortSignal) { return this.request<PairingCode>('/api/v1/studio/pairing', { method: 'POST', signal }); }
  prepare(payload: Dispatch, signal?: AbortSignal) { return this.request<Prepared>('/api/v1/panel-actions/prepare', { method: 'POST', body: JSON.stringify(payload), signal }); }
  dispatch(payload: Dispatch) { return this.request<JobView>('/api/v1/panel-actions/dispatch', { method: 'POST', body: JSON.stringify(payload) }); }
  cancel(jobId: string) { return this.request<JobView>(`/api/v1/agent-jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }); }
  native(payload: Dispatch) { return this.request<{ ok: boolean; request_id: string }>('/api/v1/native/commands', { method: 'POST', body: JSON.stringify(payload) }); }

  subscribe(receive: (message: ServerMessage) => void, status: (value: string) => void): () => void {
    let socket: WebSocket | undefined;
    let stopped = false, retry = 500;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let lastPong = Date.now();
    const open = () => {
      const url = new URL('/ws', this.host);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      status('connecting');
      socket = new WebSocket(url);
      socket.onopen = () => socket?.send(JSON.stringify({ type: 'auth', token: this.token }));
      socket.onmessage = ({ data }) => {
        if (typeof data !== 'string') return;
        const message = parseServerMessage(data);
        if (!message) return;
        if (message.type === 'auth_ok') {
          retry = 500;
          lastPong = Date.now();
          status('connected');
          heartbeat = setInterval(() => {
            if (Date.now() - lastPong > 45000) socket?.close();
            else if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }));
          }, 15000);
        } else if (message.type === 'pong') lastPong = Date.now();
        receive(message);
      };
      socket.onerror = () => socket?.close();
      socket.onclose = ({ code }) => {
        clearInterval(heartbeat);
        if (stopped) return;
        if (code === 4003 || code === 1008) { status('unauthorized'); return; }
        status('disconnected');
        reconnect = setTimeout(open, retry * (.5 + Math.random() / 2));
        retry = Math.min(30000, retry * 2);
      };
    };
    open();
    return () => { stopped = true; clearTimeout(reconnect); clearInterval(heartbeat); socket?.close(); };
  }
}

export async function pairBackend(host: string, code: string, name = 'Mobile phone', signal?: AbortSignal): Promise<PairResponse> {
  const url = new URL('/api/pair', backendHost(host));
  let response: Response;
  try {
    response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, name }), signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000) });
  } catch (reason) {
    if (signal?.aborted) throw reason;
    throw new Error('Could not reach the backend. Check the HTTPS backend address and your connection.', { cause: reason });
  }
  if (!response.ok) {
    const message = [400, 404, 410].includes(response.status)
      ? 'Pairing code is invalid or expired, or has already been used. Create a new code in Studio.'
      : response.status === 429 ? 'Too many pairing attempts. Wait a moment and try again.'
        : 'The backend could not complete pairing. Try again shortly.';
    throw new ApiError(message, response.status);
  }
  let result: unknown;
  try { result = await response.json(); } catch { throw new Error('Invalid pairing response.'); }
  if (!result || typeof result !== 'object' || !('token' in result) || !('deviceId' in result)
    || !('agentDeviceId' in result) || !('pcName' in result)
    || [result.token, result.deviceId, result.agentDeviceId, result.pcName].some((value) => typeof value !== 'string' || !value.trim())) throw new Error('Invalid pairing response.');
  return result as PairResponse;
}

const ACTIVE = new Set(['QUEUED', 'GENERATING', 'VALIDATING', 'REPAIRING', 'EXECUTING']);
export function activeJob(job: JobView) { return ACTIVE.has(job.state); }
export function jobText(job: JobView) { return job.result?.stdout?.trim() || job.reason || job.state; }

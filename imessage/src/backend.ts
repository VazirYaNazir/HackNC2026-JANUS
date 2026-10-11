// The only place the sidecar talks to the JANUS backend. No decisions are made here: the backend owns the allowlist,
// parsing, confirmations and de-duplication, and tells us what (if anything) to send back.

export interface BackendConfig {
  /** Backend origin; must be loopback. */
  url: string
  /** MESSAGING_SERVICE_TOKEN. Never logged. */
  token: string
  timeoutMs?: number
}

export interface InboundMessage {
  sender: string
  text: string
  messageId: string
}

export interface BackendReply {
  reply: string | null
  duplicate: boolean
}

export type Fetch = (input: string, init: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal }) => Promise<{
  ok: boolean
  status: number
  json(): Promise<unknown>
}>

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])

/** The service token must never travel over the network, so refuse any non-loopback backend address. */
export function assertLoopback(url: string): URL {
  const parsed = new URL(url)
  if (parsed.protocol !== 'http:' || !LOOPBACK.has(parsed.hostname)) {
    throw new Error('The backend address for the iMessage sidecar must be http on loopback (127.0.0.1).')
  }
  return parsed
}

function isReply(value: unknown): value is BackendReply {
  if (typeof value !== 'object' || value === null) return false
  const body = value as Record<string, unknown>
  return body.reply === null || typeof body.reply === 'string'
}

async function post(fetchImpl: Fetch, config: BackendConfig, path: string, payload: unknown): Promise<unknown> {
  const base = assertLoopback(config.url)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), config.timeoutMs ?? 15_000)
  try {
    const response = await fetchImpl(new URL(path, base).toString(), {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${config.token}` },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`)
    return await response.json()
  } finally {
    clearTimeout(timer)
  }
}

export async function forward(fetchImpl: Fetch, config: BackendConfig, message: InboundMessage): Promise<BackendReply> {
  const body = await post(fetchImpl, config, '/api/messaging/inbound', message)
  if (!isReply(body)) throw new Error('Unexpected reply shape from the backend')
  return { reply: body.reply, duplicate: body.duplicate === true }
}

export async function heartbeat(fetchImpl: Fetch, config: BackendConfig): Promise<void> {
  await post(fetchImpl, config, '/api/messaging/heartbeat', { connected: true })
}

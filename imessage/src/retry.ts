// Network trouble must never stop the sidecar: connecting to Photon Spectrum is retried with a growing delay, and the
// only reason to exit is missing credentials (checked before this runs). One log line per retry, never a stack trace.

export const RETRY_FIRST_MS = 2_000
export const RETRY_MAX_MS = 60_000

/** 2 s, 4 s, 8 s, ... capped at 60 s. `attempt` counts from 0. */
export function retryDelay(attempt: number): number {
  return Math.min(RETRY_MAX_MS, RETRY_FIRST_MS * 2 ** Math.max(0, attempt))
}

const NETWORK_CODES = new Set([
  'ECONNRESET', 'ECONNREFUSED', 'ECONNABORTED', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'EHOSTUNREACH', 'ENETUNREACH', 'ENETDOWN',
  'EPIPE', 'EADDRNOTAVAIL', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_SOCKET', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
  'UND_ERR_CLOSED', 'ERR_STREAM_PREMATURE_CLOSE',
])
const GRPC_UNAVAILABLE = 14
const GRPC_DEADLINE_EXCEEDED = 4
const NETWORK_MESSAGE = /fetch failed|socket hang up|network|timed? ?out|econnreset|getaddrinfo|connection (?:reset|closed|refused)|terminated|unavailable/i

/** The error plus everything it wraps (`cause`, AggregateError members), without following cycles. */
function chain(error: unknown): unknown[] {
  const seen = new Set<unknown>()
  const out: unknown[] = []
  const visit = (value: unknown, depth: number) => {
    if (value === null || value === undefined || seen.has(value) || depth > 5) return
    seen.add(value)
    out.push(value)
    if (typeof value === 'object') {
      visit((value as { cause?: unknown }).cause, depth + 1)
      const members = (value as { errors?: unknown }).errors
      if (Array.isArray(members)) for (const member of members) visit(member, depth + 1)
    }
  }
  visit(error, 0)
  return out
}

const codeOf = (value: unknown): string | number | undefined => {
  const code = (value as { code?: unknown } | null)?.code
  return typeof code === 'string' || typeof code === 'number' ? code : undefined
}

/** Connection-level trouble (DNS, resets, timeouts, an unreachable service) as opposed to a bug or a rejection. */
export function isNetworkError(error: unknown): boolean {
  return chain(error).some((item) => {
    const code = codeOf(item)
    if (typeof code === 'string' && NETWORK_CODES.has(code)) return true
    if (code === GRPC_UNAVAILABLE || code === GRPC_DEADLINE_EXCEEDED) return true
    return item instanceof Error && NETWORK_MESSAGE.test(item.message)
  })
}

/** A short, single-line summary such as "fetch failed: ECONNRESET". Never includes a stack trace. */
export function describeError(error: unknown): string {
  if (!(error instanceof Error)) return String(error ?? 'unknown error').split('\n')[0]!.slice(0, 200)
  const first = (text: string, max: number) => text.split('\n')[0]!.slice(0, max)
  const message = first(error.message || error.name, 160)
  // What the wrapped errors add: their code when they have one (ECONNRESET), otherwise their short message ("bad port").
  const details: string[] = []
  for (const item of chain(error)) {
    const code = codeOf(item)
    const detail = code !== undefined ? String(code) : item === error ? undefined : item instanceof Error ? first(item.message, 80) : undefined
    if (detail && !message.includes(detail) && !details.includes(detail)) details.push(detail)
  }
  return details.length ? `${message}: ${details.slice(0, 3).join(', ')}` : message
}

const AUTH_HINT = /\b(401|403)\b|unauthori[sz]ed|forbidden|invalid (?:project|credential|secret|token)|authentication/i

export interface RetryOptions<T> {
  connect(): Promise<T>
  log?(line: string): void
  sleep?(ms: number): Promise<void>
  /** Checked between attempts; returns null once true. */
  stopped?(): boolean
  label?: string
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/**
 * Calls `connect` until it succeeds. Every failure (network or otherwise) is logged as one line and retried after 2 s,
 * 4 s, 8 s ... up to 60 s; the process never exits from here. A rejected credential is retried too, with a hint, so
 * the sidecar keeps trying while someone fixes the settings.
 */
export async function connectWithRetry<T>({ connect, log = console.log, sleep = realSleep, stopped = () => false, label = 'Photon Spectrum' }: RetryOptions<T>): Promise<T | null> {
  for (let attempt = 0; !stopped(); attempt += 1) {
    try {
      return await connect()
    } catch (error) {
      const summary = describeError(error)
      const delay = retryDelay(attempt)
      const hint = !isNetworkError(error) && AUTH_HINT.test(summary) ? ' - check SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET' : ''
      log(`[imessage] could not connect to ${label} (${summary})${hint}; retrying in ${delay / 1000}s`)
      await sleep(delay)
    }
  }
  return null
}

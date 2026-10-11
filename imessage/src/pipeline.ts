// Message handling that can never stall the sidecar: every message runs in its own task behind a per-sender queue,
// every send has a deadline, and message IDs are persisted (and claimed *before* anything runs) so a redelivery after
// a restart cannot repeat an action. Plumbing only; the backend still makes every decision.
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { BackendReply } from './backend.js'

export const TEXT_TIMEOUT_MS = 15_000
export const HANDLER_TIMEOUT_MS = 90_000
export const PROCESSED_TTL_MS = 24 * 60 * 60 * 1000

export class TimeoutError extends Error {
  constructor(label: string, ms: number) {
    super(`${label} timed out after ${Math.round(ms / 1000)}s`)
  }
}

/** Rejects after `ms`. The underlying operation cannot be cancelled, but nothing waits on it any longer. */
export function withTimeout<T>(operation: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const deadline = new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new TimeoutError(label, ms)), ms) })
  return Promise.race([operation, deadline]).finally(() => clearTimeout(timer))
}

/**
 * One serial queue per sender, all independent: a slow or hung message delays only that sender's later messages
 * (and only until its deadline), never other senders and never the message stream itself.
 */
export class SenderQueues {
  private readonly tails = new Map<string, Promise<void>>()
  private readonly depth = new Map<string, number>()

  constructor(private readonly options: { taskTimeoutMs?: number; maxQueued?: number; onError?: (key: string, error: unknown) => void } = {}) {}

  /** Returns false (and drops the task) when a sender already has too many messages waiting. */
  enqueue(key: string, task: () => Promise<void>): boolean {
    const queued = this.depth.get(key) ?? 0
    if (queued >= (this.options.maxQueued ?? 20)) return false
    this.depth.set(key, queued + 1)
    const previous = this.tails.get(key) ?? Promise.resolve()
    const next: Promise<void> = previous
      .then(() => withTimeout(task(), this.options.taskTimeoutMs ?? HANDLER_TIMEOUT_MS, 'message handler'))
      .catch((error: unknown) => { this.options.onError?.(key, error) })
      .finally(() => {
        const remaining = (this.depth.get(key) ?? 1) - 1
        if (remaining <= 0) { this.depth.delete(key); if (this.tails.get(key) === next) this.tails.delete(key) } else this.depth.set(key, remaining)
      })
    this.tails.set(key, next)
    return true
  }

  /** Resolves when everything queued so far has settled (used by tests and shutdown). */
  async idle(): Promise<void> {
    while (this.tails.size) await Promise.allSettled([...this.tails.values()])
  }
}

/** Message IDs seen in the last 24 h, persisted so a restart cannot re-run a redelivered message. IDs are hashed. */
export class ProcessedStore {
  private readonly file: string
  private entries: Record<string, number> | null = null

  constructor(stateDir: string, private readonly ttlMs = PROCESSED_TTL_MS) {
    this.file = path.join(stateDir, 'imessage-processed.json')
  }

  private key(id: string): string {
    return createHash('sha256').update(id).digest('hex').slice(0, 32)
  }

  private load(now: number): Record<string, number> {
    if (this.entries === null) {
      try {
        const parsed: unknown = JSON.parse(readFileSync(this.file, 'utf8'))
        this.entries = typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? (parsed as Record<string, number>) : {}
      } catch {
        this.entries = {}
      }
    }
    for (const [key, seenAt] of Object.entries(this.entries)) {
      if (typeof seenAt !== 'number' || now - seenAt > this.ttlMs) delete this.entries[key]
    }
    return this.entries
  }

  has(id: string, now = Date.now()): boolean {
    return this.key(id) in this.load(now)
  }

  /** Atomically records the ID and returns true the first time; false if it was already processed. */
  claim(id: string, now = Date.now()): boolean {
    const entries = this.load(now)
    const key = this.key(id)
    if (key in entries) return false
    entries[key] = now
    mkdirSync(path.dirname(this.file), { recursive: true })
    const staged = `${this.file}.tmp`
    writeFileSync(staged, JSON.stringify(entries), { mode: 0o600 })
    renameSync(staged, this.file)
    return true
  }
}

export const mask = (value: string): string => (value.length > 4 ? `***${value.slice(-4)}` : '***')
const reason = (error: unknown): string => (error instanceof Error ? error.message : 'unknown error')

/**
 * The first line logged for every inbound message, before any filtering (text or not, allowlisted or not), so a
 * silent drop anywhere later is still visible. Masked number and message-ID prefix only; never the content.
 */
export function inboundLine(sender: string | undefined, type: string, messageId: string): string {
  return `[imessage] received from ${sender ? mask(sender) : 'unknown sender'}, type ${type} (#${messageId.slice(0, 8)})`
}

export interface HandlerDeps {
  sender: string
  text: string
  messageId: string
  forward(): Promise<BackendReply>
  sendText(text: string): Promise<unknown>
  processed: ProcessedStore
  /** Wraps the work in the typing indicator. */
  typing?<T>(work: () => Promise<T>): Promise<T>
  log?(line: string): void
  now?(): number
  timeouts?: { text?: number }
}

/** One log line per stage, tagged with the masked sender and a short message-ID prefix; never the message body. */
export async function handleMessage(deps: HandlerDeps): Promise<void> {
  const log = deps.log ?? console.log
  const now = deps.now ?? Date.now
  const tag = `[imessage] ${mask(deps.sender)} #${deps.messageId.slice(0, 8)}`
  const textMs = deps.timeouts?.text ?? TEXT_TIMEOUT_MS
  const typing = deps.typing ?? (<T>(work: () => Promise<T>) => work())

  log(`${tag} handling (${deps.text.length} chars)`)
  // Claimed before anything runs: if this process dies mid-action, the redelivery is skipped rather than repeated.
  if (!deps.processed.claim(deps.messageId, now())) {
    log(`${tag} already processed; skipping`)
    return
  }

  const sendText = async (text: string, label: string): Promise<boolean> => {
    const started = now()
    log(`${tag} sending ${label} (${text.length} chars)`)
    try {
      await withTimeout(deps.sendText(text), textMs, `sending ${label}`)
      log(`${tag} sent ${label} in ${now() - started} ms`)
      return true
    } catch (error) {
      log(`${tag} failed sending ${label}: ${reason(error)}`)
      return false
    }
  }

  await typing(async () => {
    let result: BackendReply
    try {
      result = await deps.forward()
    } catch (error) {
      log(`${tag} failed: backend: ${reason(error)}`)
      return
    }
    log(`${tag} backend reply (${result.duplicate ? 'duplicate' : result.reply === null ? 'ignored' : `${result.reply.length} chars`})`)
    if (result.duplicate || result.reply === null) return
    await sendText(result.reply, 'reply')
  })
}

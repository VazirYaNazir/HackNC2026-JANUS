// Startup greeting: one DM per allowlisted number per day. A first message to a new recipient counts against the
// Spectrum line's daily new-conversation quota (50 per line per day), so the last-sent date is persisted in .janus/
// and survives restarts. This is plumbing only; the backend still decides who may command the laptop.
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'

export const GREETING = "JANUS is online. Text 'help' for commands."

const E164 = /^\+[1-9]\d{6,14}$/

/** Valid E.164 numbers from a comma-separated list; anything else is dropped (the backend warns about it). */
export function parseAllowlist(raw: string | undefined): string[] {
  const numbers = (raw ?? '').split(',').map((entry) => entry.replace(/[\s().-]/g, '')).filter((entry) => E164.test(entry))
  return [...new Set(numbers)]
}

export const mask = (value: string): string => (value.length > 4 ? `***${value.slice(-4)}` : '***')

/** UTC calendar day, e.g. 2026-10-11. */
export const today = (now: Date): string => now.toISOString().slice(0, 10)

/** Remembers the last day each number was greeted. Numbers are stored hashed, not in the clear. */
export class GreetingStore {
  private readonly file: string

  constructor(stateDir: string) {
    this.file = path.join(stateDir, 'imessage-greeted.json')
  }

  private key(phone: string): string {
    return createHash('sha256').update(phone).digest('hex').slice(0, 32)
  }

  private read(): Record<string, string> {
    try {
      const parsed: unknown = JSON.parse(readFileSync(this.file, 'utf8'))
      return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? (parsed as Record<string, string>) : {}
    } catch {
      return {}
    }
  }

  lastGreeted(phone: string): string | undefined {
    const value = this.read()[this.key(phone)]
    return typeof value === 'string' ? value : undefined
  }

  record(phone: string, day: string): void {
    const state = this.read()
    state[this.key(phone)] = day
    mkdirSync(path.dirname(this.file), { recursive: true })
    const staged = `${this.file}.tmp`
    writeFileSync(staged, JSON.stringify(state), { mode: 0o600 })
    renameSync(staged, this.file)
  }
}

export interface Dm {
  /** The line (our number) the conversation was routed through. */
  line: string
  send(text: string): Promise<unknown>
}

export interface GreetOptions {
  phones: string[]
  store: GreetingStore
  openDm(phone: string): Promise<Dm>
  now?: () => Date
  log?: (message: string) => void
}

/** Greets each number at most once per UTC day. A failure for one number never stops the others or the sidecar. */
export async function greet({ phones, store, openDm, now = () => new Date(), log = console.log }: GreetOptions): Promise<{ sent: number; skipped: number; failed: number }> {
  const result = { sent: 0, skipped: 0, failed: 0 }
  const day = today(now())
  for (const phone of phones) {
    if (store.lastGreeted(phone) === day) {
      result.skipped += 1
      log(`[imessage] already greeted ${mask(phone)} today; skipping`)
      continue
    }
    try {
      const dm = await openDm(phone)
      await dm.send(GREETING)
      // Recorded only after a successful send, so a failed attempt is retried on the next start.
      store.record(phone, day)
      result.sent += 1
      log(`[imessage] greeted ${mask(phone)} via line ${mask(dm.line)}`)
    } catch (error) {
      result.failed += 1
      log(`[imessage] could not greet ${mask(phone)}: ${error instanceof Error ? error.message : 'unknown error'}`)
    }
  }
  return result
}

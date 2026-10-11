import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GREETING, GreetingStore, greet, mask, parseAllowlist, today, type Dm } from '../src/greeting'

const A = '+15551234567'
const B = '+15557654321'
const dirs: string[] = []
const tempDir = () => { const dir = mkdtempSync(path.join(tmpdir(), 'janus-greet-')); dirs.push(dir); return dir }
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }) })

const dm = (line = '+15550001111'): Dm & { send: ReturnType<typeof vi.fn> } => ({ line, send: vi.fn(async () => undefined) })

describe('parseAllowlist', () => {
  it('keeps valid E.164 numbers, normalizes punctuation and de-duplicates', () => {
    expect(parseAllowlist(` ${A}, +1 (555) 765-4321 ,${A}`)).toEqual([A, B])
  })
  it('drops placeholders and malformed entries', () => {
    expect(parseAllowlist('+1XXXXXXXXXX, 5551234567, nope, a@b.co, ,+0123456789')).toEqual([])
    expect(parseAllowlist(undefined)).toEqual([])
  })
})

describe('greet', () => {
  it('sends the exact greeting once and logs masked numbers only', async () => {
    const store = new GreetingStore(tempDir())
    const conversation = dm('+15550009999')
    const log = vi.fn()
    const result = await greet({ phones: [A], store, openDm: async () => conversation, log, now: () => new Date('2026-10-11T12:00:00Z') })
    expect(result).toEqual({ sent: 1, skipped: 0, failed: 0 })
    expect(conversation.send).toHaveBeenCalledExactlyOnceWith(GREETING)
    expect(GREETING).toBe("JANUS is online. Text 'help' for commands.")
    const output = log.mock.calls.flat().join('\n')
    expect(output).toContain('***4567')
    expect(output).toContain('via line ***9999')
    expect(output).not.toContain(A)
    expect(output).not.toContain('+15550009999')
  })

  it('does not greet the same number twice on the same day, even across restarts', async () => {
    const dir = tempDir()
    const now = () => new Date('2026-10-11T01:00:00Z')
    const open = vi.fn(async () => dm())
    await greet({ phones: [A], store: new GreetingStore(dir), openDm: open, now, log: () => undefined })
    const later = await greet({ phones: [A], store: new GreetingStore(dir), openDm: open, now: () => new Date('2026-10-11T23:59:00Z'), log: () => undefined })
    expect(later).toEqual({ sent: 0, skipped: 1, failed: 0 })
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('greets again the next day', async () => {
    const dir = tempDir()
    const open = vi.fn(async () => dm())
    await greet({ phones: [A], store: new GreetingStore(dir), openDm: open, now: () => new Date('2026-10-11T12:00:00Z'), log: () => undefined })
    await greet({ phones: [A], store: new GreetingStore(dir), openDm: open, now: () => new Date('2026-10-12T00:00:01Z'), log: () => undefined })
    expect(open).toHaveBeenCalledTimes(2)
  })

  it('tracks each number separately', async () => {
    const dir = tempDir()
    const open = vi.fn(async () => dm())
    const now = () => new Date('2026-10-11T12:00:00Z')
    await greet({ phones: [A], store: new GreetingStore(dir), openDm: open, now, log: () => undefined })
    const result = await greet({ phones: [A, B], store: new GreetingStore(dir), openDm: open, now, log: () => undefined })
    expect(result).toEqual({ sent: 1, skipped: 1, failed: 0 })
  })

  it('does not record a failed attempt, keeps going, and retries next time', async () => {
    const dir = tempDir()
    const now = () => new Date('2026-10-11T12:00:00Z')
    const log = vi.fn()
    const flaky = vi.fn(async (phone: string) => { if (phone === A) throw new Error('quota exceeded'); return dm() })
    const first = await greet({ phones: [A, B], store: new GreetingStore(dir), openDm: flaky, now, log })
    expect(first).toEqual({ sent: 1, skipped: 0, failed: 1 })
    expect(log.mock.calls.flat().join('\n')).toContain('could not greet ***4567: quota exceeded')
    const retry = await greet({ phones: [A, B], store: new GreetingStore(dir), openDm: async () => dm(), now, log: () => undefined })
    expect(retry).toEqual({ sent: 1, skipped: 1, failed: 0 })
  })

  it('treats a failing send (after the space opened) as a failure too', async () => {
    const conversation = dm()
    conversation.send.mockRejectedValue(new Error('offline'))
    const store = new GreetingStore(tempDir())
    const result = await greet({ phones: [A], store, openDm: async () => conversation, log: () => undefined })
    expect(result.failed).toBe(1)
    expect(store.lastGreeted(A)).toBeUndefined()
  })

  it('does nothing without numbers', async () => {
    const open = vi.fn()
    expect(await greet({ phones: [], store: new GreetingStore(tempDir()), openDm: open, log: () => undefined })).toEqual({ sent: 0, skipped: 0, failed: 0 })
    expect(open).not.toHaveBeenCalled()
  })
})

describe('GreetingStore', () => {
  it('persists the date but never the phone number in the clear', () => {
    const dir = tempDir()
    new GreetingStore(dir).record(A, '2026-10-11')
    const raw = readFileSync(path.join(dir, 'imessage-greeted.json'), 'utf8')
    expect(raw).not.toContain('5551234567')
    expect(new GreetingStore(dir).lastGreeted(A)).toBe('2026-10-11')
    expect(new GreetingStore(dir).lastGreeted(B)).toBeUndefined()
  })
  it('survives a missing, corrupt or wrongly shaped state file', () => {
    const dir = tempDir()
    const store = new GreetingStore(dir)
    expect(store.lastGreeted(A)).toBeUndefined()
    for (const content of ['not json', '[]', '"x"', 'null', '{"k": 5}']) {
      writeFileSync(path.join(dir, 'imessage-greeted.json'), content)
      expect(store.lastGreeted(A)).toBeUndefined()
      store.record(A, '2026-10-11')
      expect(store.lastGreeted(A)).toBe('2026-10-11')
    }
  })
  it('creates the state directory if needed', () => {
    const dir = path.join(tempDir(), 'nested', '.janus')
    new GreetingStore(dir).record(A, '2026-10-11')
    expect(new GreetingStore(dir).lastGreeted(A)).toBe('2026-10-11')
  })
})

describe('helpers', () => {
  it('masks and formats days', () => {
    expect(mask(A)).toBe('***4567')
    expect(mask('123')).toBe('***')
    expect(today(new Date('2026-10-11T23:59:59Z'))).toBe('2026-10-11')
  })
})

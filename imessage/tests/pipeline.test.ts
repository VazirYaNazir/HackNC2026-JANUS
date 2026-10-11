import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BackendReply } from '../src/backend'
import { ProcessedStore, SenderQueues, TimeoutError, handleMessage, inboundLine, withTimeout, type HandlerDeps } from '../src/pipeline'

const dirs: string[] = []
const tempDir = () => { const dir = mkdtempSync(path.join(tmpdir(), 'janus-pipe-')); dirs.push(dir); return dir }
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }) })

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const never = () => new Promise<never>(() => undefined)
const SENDER = '+15551234567'

const reply = (over: Partial<BackendReply> = {}): BackendReply => ({ reply: 'CPU 23%', duplicate: false, ...over })

function deps(over: Partial<HandlerDeps> & { result?: BackendReply } = {}) {
  const lines: string[] = []
  const calls = { forward: 0, text: [] as string[] }
  const { result, ...rest } = over
  const base: HandlerDeps = {
    sender: SENDER, text: 'secret message body', messageId: 'abcdef1234567890',
    processed: new ProcessedStore(tempDir()),
    forward: async () => { calls.forward += 1; return result ?? reply() },
    sendText: async (text) => { calls.text.push(text) },
    log: (line) => lines.push(line),
    timeouts: { text: 40 },
  }
  return { deps: { ...base, ...rest }, lines, calls }
}

describe('inboundLine', () => {
  it('logs every inbound message with a masked number and its type, never the content', () => {
    expect(inboundLine(SENDER, 'text', 'abcdef1234567890')).toBe('[imessage] received from ***4567, type text (#abcdef12)')
    expect(inboundLine(SENDER, 'attachment', 'zz99')).toBe('[imessage] received from ***4567, type attachment (#zz99)')
    expect(inboundLine(undefined, 'reaction', 'id')).toBe('[imessage] received from unknown sender, type reaction (#id)')
    expect(inboundLine('a@b.co', 'text', 'm')).not.toContain('a@b.co')
  })
})

describe('withTimeout', () => {
  it('passes results and errors through and rejects on deadline', async () => {
    await expect(withTimeout(Promise.resolve(5), 50, 'x')).resolves.toBe(5)
    await expect(withTimeout(Promise.reject(new Error('boom')), 50, 'x')).rejects.toThrow('boom')
    await expect(withTimeout(never(), 20, 'sending attachment')).rejects.toThrow(new TimeoutError('sending attachment', 20))
  })
  it('does not leave an unhandled rejection when the slow operation fails after the deadline', async () => {
    const late = new Promise<never>((_resolve, reject) => setTimeout(() => reject(new Error('late failure')), 40))
    await expect(withTimeout(late, 10, 'x')).rejects.toBeInstanceOf(TimeoutError)
    await sleep(60)
  })
})

describe('SenderQueues', () => {
  it('runs one sender serially and different senders concurrently', async () => {
    const queues = new SenderQueues()
    const order: string[] = []
    queues.enqueue('a', async () => { order.push('a1 start'); await sleep(50); order.push('a1 end') })
    queues.enqueue('a', async () => { order.push('a2') })
    queues.enqueue('b', async () => { order.push('b1') })
    await queues.idle()
    expect(order).toEqual(['a1 start', 'b1', 'a1 end', 'a2'])
  })

  it('a hung message delays only its own sender, and only until its deadline', async () => {
    const errors: string[] = []
    const queues = new SenderQueues({ taskTimeoutMs: 40, onError: (key, error) => errors.push(`${key}: ${(error as Error).message}`) })
    const done: string[] = []
    queues.enqueue('stuck', never)
    queues.enqueue('stuck', async () => { done.push('stuck-2') })
    queues.enqueue('other', async () => { done.push('other') })
    await sleep(10)
    expect(done).toEqual(['other'])
    await queues.idle()
    expect(done).toEqual(['other', 'stuck-2'])
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain('message handler timed out')
  })

  it('survives a throwing task and keeps processing', async () => {
    const onError = vi.fn()
    const queues = new SenderQueues({ onError })
    const ran = vi.fn()
    queues.enqueue('a', async () => { throw new Error('bad') })
    queues.enqueue('a', async () => { ran() })
    await queues.idle()
    expect(onError).toHaveBeenCalledTimes(1)
    expect(ran).toHaveBeenCalledTimes(1)
  })

  it('drops work beyond the per-sender limit instead of growing without bound', async () => {
    const queues = new SenderQueues({ maxQueued: 2 })
    expect(queues.enqueue('a', () => sleep(30))).toBe(true)
    expect(queues.enqueue('a', async () => undefined)).toBe(true)
    expect(queues.enqueue('a', async () => undefined)).toBe(false)
    await queues.idle()
    expect(queues.enqueue('a', async () => undefined)).toBe(true)
  })
})

describe('ProcessedStore', () => {
  it('claims an id once, across instances (restarts)', () => {
    const dir = tempDir()
    expect(new ProcessedStore(dir).claim('m1')).toBe(true)
    expect(new ProcessedStore(dir).claim('m1')).toBe(false)
    expect(new ProcessedStore(dir).claim('m2')).toBe(true)
  })
  it('forgets ids after 24 hours', () => {
    const dir = tempDir()
    const store = new ProcessedStore(dir)
    store.claim('m1', 1_000)
    expect(store.has('m1', 1_000 + 24 * 3600_000 - 1)).toBe(true)
    expect(new ProcessedStore(dir).claim('m1', 1_000 + 24 * 3600_000 + 1)).toBe(true)
  })
  it('stores hashes, not ids, and tolerates a corrupt file', () => {
    const dir = tempDir()
    new ProcessedStore(dir).claim('plain-message-id')
    const file = path.join(dir, 'imessage-processed.json')
    expect(readFileSync(file, 'utf8')).not.toContain('plain-message-id')
    for (const content of ['garbage', '[]', 'null', '{"k":"x"}']) {
      writeFileSync(file, content)
      expect(new ProcessedStore(dir).claim('fresh')).toBe(true)
    }
  })
})

describe('handleMessage', () => {
  it('claims the id before running the action, so a crash mid-action cannot be replayed', async () => {
    const dir = tempDir()
    const crashing = deps({ processed: new ProcessedStore(dir), forward: async () => { throw new Error('process died') } })
    await handleMessage(crashing.deps)
    const restarted = deps({ processed: new ProcessedStore(dir) })
    await handleMessage(restarted.deps)
    expect(restarted.calls.forward).toBe(0)
    expect(restarted.lines.join('\n')).toContain('already processed')
  })

  it('claims the id even while the action is still running', async () => {
    const dir = tempDir()
    const store = new ProcessedStore(dir)
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    const first = deps({ processed: store, forward: async () => { await gate; return reply() } })
    const running = handleMessage(first.deps)
    await sleep(10)
    expect(new ProcessedStore(dir).claim('abcdef1234567890')).toBe(false)
    release()
    await running
  })

  it('sends a text reply and logs every stage without the body or full numbers', async () => {
    const t = deps()
    await handleMessage(t.deps)
    expect(t.calls.text).toEqual(['CPU 23%'])
    const log = t.lines.join('\n')
    for (const stage of ['handling (19 chars)', 'backend reply (7 chars)', 'sending reply (7 chars)', 'sent reply in']) expect(log).toContain(stage)
    expect(log).toContain('***4567 #abcdef12')
    expect(log).not.toContain('secret message body')
    expect(log).not.toContain(SENDER)
    expect(t.lines.map((line) => line.replace(/.*#abcdef12 /, '').split(' ')[0])).toEqual(['handling', 'backend', 'sending', 'sent'])
  })

  it('times out a hung text send and returns instead of hanging', async () => {
    const t = deps({ sendText: never })
    const started = Date.now()
    await handleMessage(t.deps)
    expect(Date.now() - started).toBeLessThan(500)
    expect(t.lines.join('\n')).toContain('failed sending reply: sending reply timed out')
  })

  it('logs a backend failure and stays quiet to the user', async () => {
    const t = deps({ forward: async () => { throw new Error('Backend returned HTTP 503') } })
    await handleMessage(t.deps)
    expect(t.calls.text).toEqual([])
    expect(t.lines.join('\n')).toContain('failed: backend: Backend returned HTTP 503')
  })

  it('sends nothing for ignored senders or duplicates reported by the backend', async () => {
    for (const result of [reply({ reply: null }), reply({ duplicate: true })]) {
      const t = deps({ result })
      await handleMessage(t.deps)
      expect(t.calls.text).toEqual([])
    }
  })

  it('wraps the work in the typing indicator when one is supplied', async () => {
    const events: string[] = []
    const t = deps({ typing: async (work) => { events.push('typing on'); const value = await work(); events.push('typing off'); return value }, sendText: async () => { events.push('send') } })
    await handleMessage(t.deps)
    expect(events).toEqual(['typing on', 'send', 'typing off'])
  })

  it('a stuck message cannot stall others: full pipeline through the queues', async () => {
    const queues = new SenderQueues({ taskTimeoutMs: 80 })
    const store = new ProcessedStore(tempDir())
    const sentTo: string[] = []
    const run = (sender: string, id: string, hold: () => Promise<unknown>) => queues.enqueue(sender, () => handleMessage({
      sender, text: 'x', messageId: id, processed: store, log: () => undefined, timeouts: { text: 30 },
      forward: async () => reply(), sendText: async () => { await hold(); sentTo.push(sender) },
    }))
    run('+15550000001', 'id-stuck-0001', never)
    run('+15550000002', 'id-fast-00002', async () => undefined)
    await sleep(15)
    expect(sentTo).toEqual(['+15550000002'])
    await queues.idle()
  })
})

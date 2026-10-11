import { describe, expect, it, vi } from 'vitest'
import { assertLoopback, forward, heartbeat, type BackendConfig, type Fetch } from '../src/backend'

const config: BackendConfig = { url: 'http://127.0.0.1:8000', token: 't'.repeat(40) }
const message = { sender: '+15551234567', text: 'cpu', messageId: 'abc' }
const respond = (body: unknown, status = 200): Fetch => vi.fn(async () => ({ ok: status < 400, status, json: async () => body }))

describe('forward', () => {
  it('posts sender, text and messageId with the service token and returns the backend reply', async () => {
    const fetchImpl = respond({ reply: 'CPU 23%', duplicate: false })
    await expect(forward(fetchImpl, config, message)).resolves.toEqual({ reply: 'CPU 23%', duplicate: false })
    const [url, init] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('http://127.0.0.1:8000/api/messaging/inbound')
    expect(init.method).toBe('POST')
    expect(init.headers.authorization).toBe(`Bearer ${config.token}`)
    expect(JSON.parse(init.body)).toEqual(message)
  })

  it('returns an empty reply for senders the backend ignores', async () => {
    await expect(forward(respond({ reply: null, duplicate: false }), config, message)).resolves.toEqual({ reply: null, duplicate: false })
  })

  it('rejects HTTP errors and malformed bodies instead of guessing', async () => {
    await expect(forward(respond({}, 401), config, message)).rejects.toThrow('HTTP 401')
    await expect(forward(respond('nope'), config, message)).rejects.toThrow('Unexpected reply shape')
    await expect(forward(respond({ reply: 5 }), config, message)).rejects.toThrow('Unexpected reply shape')
  })

  it('aborts a hung backend after the timeout', async () => {
    const hung: Fetch = (_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))))
    await expect(forward(hung, { ...config, timeoutMs: 20 }, message)).rejects.toThrow('aborted')
  })

  it('never sends the token to a non-loopback address', async () => {
    const fetchImpl = respond({ reply: null })
    for (const url of ['http://example.com', 'https://127.0.0.1:8000', 'http://192.168.1.5:8000', 'http://127.0.0.1.evil.com']) {
      await expect(forward(fetchImpl, { ...config, url }, message)).rejects.toThrow('loopback')
    }
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})

describe('heartbeat and assertLoopback', () => {
  it('posts a heartbeat to the same authenticated endpoint family', async () => {
    const fetchImpl = respond({ ok: true })
    await heartbeat(fetchImpl, config)
    expect((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe('http://127.0.0.1:8000/api/messaging/heartbeat')
  })
  it('accepts the loopback spellings', () => {
    for (const url of ['http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000']) expect(() => assertLoopback(url)).not.toThrow()
  })
})

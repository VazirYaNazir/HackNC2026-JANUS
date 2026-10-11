import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { RETRY_MAX_MS, connectWithRetry, describeError, isNetworkError, retryDelay } from '../src/retry'

const fetchFailed = (code = 'ECONNRESET') => Object.assign(new TypeError('fetch failed'), { cause: Object.assign(new Error('read ECONNRESET'), { code }) })

describe('retryDelay', () => {
  it('doubles from 2 s and stops growing at 60 s', () => {
    expect([0, 1, 2, 3, 4, 5, 6, 20].map(retryDelay)).toEqual([2_000, 4_000, 8_000, 16_000, 32_000, 60_000, 60_000, 60_000])
    expect(retryDelay(-3)).toBe(2_000)
    expect(RETRY_MAX_MS).toBe(60_000)
  })
})

describe('describeError', () => {
  it('summarises Node fetch failures with the underlying code and no stack', () => {
    const text = describeError(fetchFailed())
    expect(text).toBe('fetch failed: ECONNRESET')
    expect(text).not.toContain('\n')
    expect(text).not.toContain(' at ')
  })
  it('reads codes out of AggregateError members (dual-stack connection attempts)', () => {
    const error = Object.assign(new TypeError('fetch failed'), { cause: new AggregateError([Object.assign(new Error('a'), { code: 'ENETUNREACH' }), Object.assign(new Error('b'), { code: 'ETIMEDOUT' })]) })
    expect(describeError(error)).toBe('fetch failed: ENETUNREACH, ETIMEDOUT')
  })
  it('is safe for odd values and long or multi-line messages', () => {
    expect(describeError('plain string')).toBe('plain string')
    expect(describeError(undefined)).toBe('unknown error')
    expect(describeError(new Error('first line\nsecond line'))).toBe('first line')
    expect(describeError(new Error('x'.repeat(500))).length).toBeLessThanOrEqual(160)
    const cyclic: { cause?: unknown } = new Error('loop')
    cyclic.cause = cyclic
    expect(describeError(cyclic)).toBe('loop')
  })
  it('falls back to the wrapped error message when it has no code (e.g. fetch rejecting a port)', () => {
    expect(describeError(Object.assign(new TypeError('fetch failed'), { cause: new Error('bad port') }))).toBe('fetch failed: bad port')
    expect(describeError(Object.assign(new TypeError('fetch failed'), { cause: new Error('fetch failed') }))).toBe('fetch failed')
  })
  it('includes the error code when the message does not', () => {
    expect(describeError(Object.assign(new Error('connect failed'), { code: 'ENOTFOUND' }))).toBe('connect failed: ENOTFOUND')
  })
})

describe('isNetworkError', () => {
  it('recognises connection-level problems', () => {
    for (const error of [fetchFailed(), fetchFailed('ETIMEDOUT'), fetchFailed('UND_ERR_CONNECT_TIMEOUT'), new Error('socket hang up'),
      Object.assign(new Error('x'), { code: 'EAI_AGAIN' }), Object.assign(new Error('14 UNAVAILABLE: No connection established'), { code: 14 }),
      Object.assign(new Error('deadline'), { code: 4 }), new TypeError('fetch failed')]) {
      expect(isNetworkError(error)).toBe(true)
    }
  })
  it('does not treat bugs or rejections as network problems', () => {
    for (const error of [new TypeError("Cannot read properties of undefined (reading 'x')"), new Error('Invalid project secret'), new RangeError('bad'), 'oops', null, undefined,
      Object.assign(new Error('x'), { code: 'ERR_INVALID_ARG_TYPE' })]) {
      expect(isNetworkError(error)).toBe(false)
    }
  })
})

describe('connectWithRetry', () => {
  const quiet = () => ({ log: vi.fn<(line: string) => void>(), sleep: vi.fn(async () => undefined) })

  it('returns straight away when the first attempt works', async () => {
    const { log, sleep } = quiet()
    await expect(connectWithRetry({ connect: async () => 'app', log, sleep })).resolves.toBe('app')
    expect(log).not.toHaveBeenCalled()
    expect(sleep).not.toHaveBeenCalled()
  })

  it('retries network failures with 2 s -> 60 s backoff, one log line each, never throwing', async () => {
    const { log, sleep } = quiet()
    let calls = 0
    const connect = vi.fn(async () => {
      calls += 1
      if (calls <= 8) throw fetchFailed()
      return 'app'
    })
    await expect(connectWithRetry({ connect, log, sleep })).resolves.toBe('app')
    expect(sleep.mock.calls.map((call) => call[0])).toEqual([2_000, 4_000, 8_000, 16_000, 32_000, 60_000, 60_000, 60_000])
    expect(log).toHaveBeenCalledTimes(8)
    expect(log.mock.calls[0]![0]).toBe('[imessage] could not connect to Photon Spectrum (fetch failed: ECONNRESET); retrying in 2s')
    expect(log.mock.calls[7]![0]).toContain('retrying in 60s')
    for (const [line] of log.mock.calls) {
      expect(line).not.toContain('\n')
      expect(line).not.toMatch(/\bat \S+ \(/)   // no stack frames
    }
  })

  it('keeps retrying a rejected credential, with a hint, instead of exiting', async () => {
    const { log, sleep } = quiet()
    let calls = 0
    const connect = async () => {
      calls += 1
      if (calls < 3) throw new Error('Request failed with status 401 Unauthorized')
      return 'app'
    }
    await expect(connectWithRetry({ connect, log, sleep })).resolves.toBe('app')
    expect(log.mock.calls[0]![0]).toContain('check SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET')
  })

  it('does not add the credential hint to plain network failures', async () => {
    const { log, sleep } = quiet()
    let calls = 0
    await connectWithRetry({ connect: async () => { calls += 1; if (calls === 1) throw fetchFailed('ETIMEDOUT'); return 1 }, log, sleep })
    expect(log.mock.calls[0]![0]).not.toContain('SPECTRUM_PROJECT_ID')
  })

  it('stops (returning null) once asked to, without another attempt', async () => {
    const { log, sleep } = quiet()
    let stop = false
    const connect = vi.fn(async () => { stop = true; throw fetchFailed() })
    await expect(connectWithRetry({ connect, log, sleep, stopped: () => stop })).resolves.toBeNull()
    expect(connect).toHaveBeenCalledTimes(1)
  })
})

describe('the sidecar entry', () => {
  const source = readFileSync(new URL('../src/index.ts', import.meta.url), 'utf8')

  it('sets IPv4-first DNS before spectrum-ts is ever loaded, and loads it dynamically', () => {
    const dnsCall = source.indexOf("dns.setDefaultResultOrder('ipv4first')")
    expect(dnsCall).toBeGreaterThan(-1)
    expect(source).not.toMatch(/^import[^\n]*from 'spectrum-ts/m)           // no static import that would be hoisted above the call
    expect(source.indexOf("import('spectrum-ts')")).toBeGreaterThan(dnsCall)
    expect(source.indexOf("import('spectrum-ts/providers/imessage')")).toBeGreaterThan(dnsCall)
  })

  it('exits only for missing credentials; connecting goes through the retry wrapper', () => {
    expect(source.match(/process\.exit\(1\)/g)?.length).toBe(2)             // missing credential, and a non-network bug
    expect(source).toContain('connectWithRetry({ connect: createApp')
    expect(source).not.toMatch(/await Spectrum\(/)                          // never an unguarded init
  })
})

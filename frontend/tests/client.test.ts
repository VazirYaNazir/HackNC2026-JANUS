import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, PanelClient, activeJob, backendHost, jobText, pairBackend } from '@janus/client'
import { parseServerMessage, type JobView } from '@janus/schema'
import { mergeHistory, recordJob } from '../../shared/client/src/jobs'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

describe('shared frontend transport', () => {
  it('keeps completed results when older HTTP or history updates arrive late', () => {
    const completed = { job_id: 'fresh', state: 'COMPLETED', elapsed_ms: 30, result: { stdout: '42' } } as JobView
    const queued = { ...completed, state: 'QUEUED', elapsed_ms: 1, result: null }
    expect(recordJob([completed], queued)).toEqual([completed])
    expect(mergeHistory([completed], [queued])).toEqual([completed])
    const validation = { ...queued, state: 'VALIDATING', elapsed_ms: 20 }
    expect(recordJob([validation], queued)).toEqual([validation])
    expect(recordJob([queued], completed)).toEqual([completed])
    const confirmed = { ...completed, cancellation_confirmed: true }
    expect(recordJob([completed], confirmed)).toEqual([confirmed])
    expect(recordJob(Array.from({ length: 50 }, (_, n) => ({ ...completed, job_id: String(n) })), completed)).toHaveLength(50)
    expect(mergeHistory([completed], [{ ...completed, job_id: 'other' }])).toHaveLength(2)
  })
  it('rejects malformed or unknown frames before changing UI state', () => {
    for (const raw of ['null', '[]', 'bad json', '{"type":"unknown"}', '{"type":"job_update"}', '{"type":"agent_presence"}', '{"type":"error","message":1}', '{"type":"data","source":"cpu","value":{}}']) expect(parseServerMessage(raw)).toBeNull()
    expect(parseServerMessage('{"type":"data","source":"cpu","value":null}')).toEqual({ type: 'data', source: 'cpu', value: null })
    expect(parseServerMessage('{"type":"auth_ok"}')).toEqual({ type: 'auth_ok' })
  })
  it('allows verified HTTPS or loopback HTTP and rejects credential-bearing addresses', () => {
    expect(backendHost('https://backend.example/')).toBe('https://backend.example')
    expect(backendHost('http://127.0.0.1:8000')).toBe('http://127.0.0.1:8000')
    for (const host of ['http://remote.example', 'https://user:secret@backend.example', 'https://backend.example/?token=secret', 'https://backend.example/path', 'file:///tmp/code']) expect(() => backendHost(host)).toThrow()
  })

  it('preserves custom headers, uses scoped bearer auth and rejects cross-origin paths', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 1 })))
    vi.stubGlobal('fetch', fetch)
    const client = new PanelClient({ host: 'https://backend.example', token: 'controller-secret' })
    await client.request('/api/layout', { headers: new Headers({ 'X-Request-ID': 'request' }) })
    const [url, init] = fetch.mock.calls[0]
    expect(String(url)).toBe('https://backend.example/api/layout')
    expect(init.headers.get('Authorization')).toBe('Bearer controller-secret')
    expect(init.headers.get('X-Request-ID')).toBe('request')
    await expect(client.request('//attacker.example')).rejects.toThrow('Invalid API path')
    await expect(client.request('/\\attacker.example')).rejects.toThrow('Invalid API path')
    await expect(client.request('https://attacker.example')).rejects.toThrow('Invalid API path')
  })

  it('pairs using the real API and rejects failed or malformed responses', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ token: 'phone', agentDeviceId: 'laptop', pcName: 'PC', deviceId: 'phone-id' })))
    vi.stubGlobal('fetch', fetch)
    expect((await pairBackend('https://backend.example', 'code')).agentDeviceId).toBe('laptop')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ code: 'code', name: 'Mobile phone' })
    fetch.mockResolvedValue(new Response('{}', { status: 400 }))
    await expect(pairBackend('https://backend.example', 'wrong')).rejects.toThrow('invalid or expired')
    fetch.mockResolvedValue(new Response('{}'))
    await expect(pairBackend('https://backend.example', 'wrong')).rejects.toThrow('Invalid pairing response')
  })

  it('distinguishes pairing network failures, rate limits, and server failures from bad codes', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetch)
    await expect(pairBackend('https://backend.example', 'code')).rejects.toThrow('Could not reach the backend')
    fetch.mockResolvedValue(new Response('{}', { status: 429 }))
    await expect(pairBackend('https://backend.example', 'code')).rejects.toMatchObject({ status: 429, message: expect.stringContaining('Too many pairing attempts') })
    fetch.mockResolvedValue(new Response('{}', { status: 503 }))
    await expect(pairBackend('https://backend.example', 'code')).rejects.toBeInstanceOf(ApiError)
    await expect(pairBackend('https://backend.example', 'code')).rejects.toThrow('could not complete pairing')
  })

  it('rejects empty, incomplete, non-object, and unreadable pairing responses', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    for (const value of [null, [], { token: 'phone', agentDeviceId: 'laptop', pcName: 'PC' }, { token: '', deviceId: 'id', agentDeviceId: 'laptop', pcName: 'PC' }]) {
      fetch.mockResolvedValue(new Response(JSON.stringify(value)))
      await expect(pairBackend('https://backend.example', 'code')).rejects.toThrow('Invalid pairing response')
    }
    fetch.mockResolvedValue(new Response('not JSON'))
    await expect(pairBackend('https://backend.example', 'code')).rejects.toThrow('Invalid pairing response')
  })

  it('propagates a caller cancellation instead of presenting it as an unreachable backend', async () => {
    const controller = new AbortController()
    controller.abort()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Cancelled', 'AbortError')))
    await expect(pairBackend('https://backend.example', 'code', 'Phone', controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('propagates server errors and sends fresh IDs with only explicitly chosen reuse', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    const client = new PanelClient({ host: 'https://backend.example', token: 'credential' })
    await client.dispatch({ widget_id: 'w', job_id: 'fresh' })
    expect(JSON.parse(fetch.mock.calls[0][1].body)).not.toHaveProperty('program_id')
    await client.dispatch({ widget_id: 'w', job_id: 'another', program_id: 'approved' })
    expect(JSON.parse(fetch.mock.calls[1][1].body).program_id).toBe('approved')
    fetch.mockResolvedValue(new Response(JSON.stringify({ detail: 'DUPLICATE_JOB' }), { status: 409 }))
    await expect(client.dispatch({ widget_id: 'w', job_id: 'fresh' })).rejects.toThrow('DUPLICATE_JOB')
    fetch.mockResolvedValue(new Response('unavailable', { status: 503 }))
    await expect(client.layout()).rejects.toThrow('503')
  })

  it('uses message authentication, heartbeats, reconnects, and releases timers on unmount', () => {
    vi.useFakeTimers()
    class Socket {
      static OPEN = 1
      static instances: Socket[] = []
      readyState = 1
      onopen?: () => void
      onmessage?: (message: { data: string }) => void
      onclose?: (message: { code: number }) => void
      onerror?: () => void
      send = vi.fn()
      close = vi.fn(() => this.onclose?.({ code: 1000 }))
      constructor(readonly url: URL) { Socket.instances.push(this) }
    }
    vi.stubGlobal('WebSocket', Socket)
    const status = vi.fn(), receive = vi.fn()
    const close = new PanelClient({ host: 'https://backend.example', token: 'secret' }).subscribe(receive, status)
    const socket = Socket.instances[0]
    expect(String(socket.url)).toBe('wss://backend.example/ws')
    expect(String(socket.url)).not.toContain('secret')
    socket.onopen?.()
    expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({ type: 'auth', token: 'secret' })
    socket.onmessage?.({ data: '{"type":"auth_ok"}' })
    vi.advanceTimersByTime(15000)
    expect(socket.send).toHaveBeenLastCalledWith('{"type":"ping"}')
    socket.onmessage?.({ data: '{"type":"pong"}' })
    socket.onclose?.({ code: 1006 })
    vi.advanceTimersByTime(500)
    expect(Socket.instances).toHaveLength(2)
    Socket.instances[1].onclose?.({ code: 4003 })
    vi.advanceTimersByTime(30000)
    expect(Socket.instances).toHaveLength(2)
    close()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('distinguishes active jobs and displays only returned output or reasons', () => {
    const base = { state: 'EXECUTING', reason: null, result: null } as JobView
    expect(activeJob(base)).toBe(true)
    expect(jobText(base)).toBe('EXECUTING')
    expect(jobText({ ...base, state: 'REJECTED', reason: 'AUTHORIZATION_REQUIRED' })).toBe('AUTHORIZATION_REQUIRED')
    expect(jobText({ ...base, state: 'COMPLETED', result: { stdout: '42\n' } })).toBe('42')
  })
})

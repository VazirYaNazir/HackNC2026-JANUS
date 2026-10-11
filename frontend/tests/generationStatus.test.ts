import { describe, expect, it } from 'vitest'
import type { Session } from '@janus/client'
import { generationStatus } from '../src/generationStatus'

const session = (overrides: Partial<Session> = {}, target: Partial<NonNullable<Session['target']>> = {}): Session => ({
  device_id: 'laptop', online: true, generation_available: true,
  target: { platform: 'Windows', architecture: 'x86_64', build_boundary_available: true, execution_boundary_available: true, ...target },
  ...overrides,
})

describe('generationStatus', () => {
  it('asks to connect first', () => expect(generationStatus(null).tone).toBe('idle'))
  it('reports an offline laptop', () => expect(generationStatus(session({ online: false, target: null })).text).toContain('offline'))
  it('is ready when a key and an isolation adapter are both present', () => expect(generationStatus(session())).toMatchObject({ tone: 'ready', links: [] }))
  it('says generation works but programs cannot run without an isolation adapter, with a fix link', () => {
    const status = generationStatus(session({}, { execution_boundary_available: false }))
    expect(status.text).toBe('Generation available; this laptop can’t run generated programs yet (no isolation adapter). Built-in controls still work.')
    expect(status.links.map((link) => link.href)).toEqual([expect.stringContaining('#isolation-adapter')])
  })
  it('treats a missing build boundary the same way', () => expect(generationStatus(session({}, { build_boundary_available: false })).tone).toBe('warning'))
  it('reports a missing Gemini key', () => {
    const status = generationStatus(session({ generation_available: false }))
    expect(status.text).toContain('no Gemini key')
    expect(status.links[0].href).toContain('#gemini-key')
  })
  it('reports both problems with both links', () => expect(generationStatus(session({ generation_available: false }, { execution_boundary_available: false })).links).toHaveLength(2))
  it('assumes a key is present when an older backend does not report it', () => expect(generationStatus(session({ generation_available: undefined })).tone).toBe('ready'))
})

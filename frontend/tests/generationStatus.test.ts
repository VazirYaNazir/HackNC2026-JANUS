import { describe, expect, it } from 'vitest'
import type { Session } from '@janus/client'
import { agentHelpView } from '../src/agentHelp'
import { generationStatus, messagingLabel } from '../src/generationStatus'

const session = (overrides: Partial<Session> = {}, target: Partial<NonNullable<Session['target']>> = {}): Session => ({
  device_id: 'laptop', online: true, generation_available: true,
  target: { platform: 'Windows', architecture: 'x86_64', build_boundary_available: true, execution_boundary_available: true, ...target },
  ...overrides,
})

describe('generationStatus', () => {
  it('asks to connect first', () => expect(generationStatus(null).tone).toBe('idle'))
  it('keeps source generation available with an offline laptop', () => {
    const status = generationStatus(session({ online: false, target: null }))
    expect(status.text).toContain('generation is available while your laptop agent is offline')
    expect(status.text).toContain('Review and download source')
  })
  it('reports a missing Gemini key even when the laptop is offline', () => {
    expect(generationStatus(session({ online: false, target: null, generation_available: false })).text).toContain('no Gemini key')
  })
  it('is ready when a key and an isolation adapter are both present', () => expect(generationStatus(session())).toMatchObject({ tone: 'ready', links: [] }))
  it('says generation works but programs cannot run without an isolation adapter, with a fix link', () => {
    const status = generationStatus(session({}, { execution_boundary_available: false }))
    expect(status.text).toContain('Start Docker Desktop and restart JANUS')
    expect(status.text).toContain('Built-in controls still work')
    expect(status.links.map((link) => link.href)).toEqual(['https://www.docker.com/products/docker-desktop/'])
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

describe('messagingLabel', () => {
  it('says nothing while unknown', () => expect(messagingLabel(null)).toBeNull())
  it('reports connected only when enabled and the sidecar is heartbeating', () => {
    expect(messagingLabel({ enabled: true, connected: true })).toBe('iMessage: connected')
    expect(messagingLabel({ enabled: true, connected: false })).toBe('iMessage: off')
    expect(messagingLabel({ enabled: false, connected: false })).toBe('iMessage: off')
    expect(messagingLabel({ enabled: false, connected: true })).toBe('iMessage: off')
  })
  it('distinguishes setup, an explicit disable, and a missing sidecar heartbeat', () => {
    expect(messagingLabel({ enabled: false, connected: false, reason: 'MESSAGING_NOT_CONFIGURED', missing: ['SPECTRUM_PROJECT_ID'] })).toBe('iMessage: setup required')
    expect(messagingLabel({ enabled: true, connected: false, reason: 'MESSAGING_DISABLED' })).toBe('iMessage: disabled')
    expect(messagingLabel({ enabled: true, connected: false, reason: 'SIDECAR_NOT_CONNECTED' })).toBe('iMessage: waiting for connection')
    expect(messagingLabel({ enabled: true, connected: true, reason: null })).toBe('iMessage: connected')
  })
})

describe('agentHelpView', () => {
  it('offers the command to copy outside Electron', () => expect(agentHelpView({ desktop: false, running: false, waiting: false })).toBe('copy-command'))
  it('offers Start only in Electron with no agent process and no start in flight', () => expect(agentHelpView({ desktop: true, running: false, waiting: false })).toBe('start'))
  it('hides Start while an agent exists or was just started, so a second one is never spawned', () => {
    expect(agentHelpView({ desktop: true, running: true, waiting: false })).toBe('waiting')
    expect(agentHelpView({ desktop: true, running: false, waiting: true })).toBe('waiting')
  })
})

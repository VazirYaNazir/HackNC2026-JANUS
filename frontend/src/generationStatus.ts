import { executionUnavailable, type Session } from '@janus/client'

const DOCS = 'https://github.com/VazirYaNazir/HackNC2026-Janus/blob/main/README.md'
const GEMINI = { label: 'Add a Gemini key', href: `${DOCS}#gemini-key` }
const DOCKER = { label: 'Download Docker Desktop', href: 'https://www.docker.com/products/docker-desktop/' }

export interface GenerationStatus {
  tone: 'ready' | 'warning' | 'idle'
  text: string
  links: { label: string; href: string }[]
}

/** What the user can expect from the assistant, so they find out before generating rather than after. */
export function generationStatus(session: Session | null): GenerationStatus {
  if (!session) return { tone: 'idle', text: 'Connect your backend to generate C source.', links: [] }
  const noKey = session.generation_available === false
  const noIsolation = session.target != null && (!session.target.build_boundary_available || !session.target.execution_boundary_available)
  if (noKey && noIsolation) {
    return { tone: 'warning', text: 'Generation unavailable (no Gemini key), and this laptop’s isolated worker is unavailable. Start Docker Desktop, then restart JANUS. Built-in controls still work.', links: [GEMINI, DOCKER] }
  }
  if (noKey) return { tone: 'warning', text: 'Generation unavailable: the backend has no Gemini key. Built-in controls still work.', links: [GEMINI] }
  if (!session.online || !session.target) return { tone: 'warning', text: 'C source generation is available while your laptop agent is offline. Review and download source; connect the agent to request execution.', links: [] }
  if (noIsolation) return { tone: 'warning', text: `C source generation is available. Review and download source. ${executionUnavailable(session)} Built-in controls still work.`, links: [DOCKER] }
  return { tone: 'ready', text: 'C source generation and isolated execution are ready. Generate a program to review and run, or select a control to save a reusable action.', links: [] }
}

export interface MessagingStatus {
  enabled: boolean
  connected: boolean
  reason?: string | null
  missing?: string[]
}

/** Small "iMessage: connected / off" label; null while unknown (no backend, or an older backend without the endpoint). */
export function messagingLabel(status: MessagingStatus | null): string | null {
  if (!status) return null
  if (status.enabled && status.connected) return 'iMessage: connected'
  if (status.reason === 'MESSAGING_DISABLED') return 'iMessage: disabled'
  if (status.reason === 'MESSAGING_NOT_CONFIGURED') return 'iMessage: setup required'
  if (status.reason === 'SIDECAR_NOT_CONNECTED') return 'iMessage: waiting for connection'
  return 'iMessage: off'
}

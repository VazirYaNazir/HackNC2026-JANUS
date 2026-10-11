import type { Session } from '@janus/client'

const DOCS = 'https://github.com/VazirYaNazir/HackNC2026-Janus/blob/main/README.md'
const GEMINI = { label: 'Add a Gemini key', href: `${DOCS}#gemini-key` }
const ISOLATION = { label: 'About isolation adapters', href: `${DOCS}#isolation-adapter` }

export interface GenerationStatus {
  tone: 'ready' | 'warning' | 'idle'
  text: string
  links: { label: string; href: string }[]
}

/** What the user can expect from the assistant, so they find out before generating rather than after. */
export function generationStatus(session: Session | null): GenerationStatus {
  if (!session) return { tone: 'idle', text: 'Connect your backend to see whether the assistant can generate controls.', links: [] }
  if (!session.online || !session.target) return { tone: 'warning', text: 'Your laptop agent is offline, so generated controls cannot be built or run.', links: [] }
  const noKey = session.generation_available === false
  const noIsolation = !session.target.build_boundary_available || !session.target.execution_boundary_available
  if (noKey && noIsolation) {
    return { tone: 'warning', text: 'Generation unavailable (no Gemini key), and this laptop can’t run generated programs yet (no isolation adapter). Built-in controls still work.', links: [GEMINI, ISOLATION] }
  }
  if (noKey) return { tone: 'warning', text: 'Generation unavailable: the backend has no Gemini key. Built-in controls still work.', links: [GEMINI] }
  if (noIsolation) return { tone: 'warning', text: 'Generation available; this laptop can’t run generated programs yet (no isolation adapter). Built-in controls still work.', links: [ISOLATION] }
  return { tone: 'ready', text: 'Generation ready.', links: [] }
}

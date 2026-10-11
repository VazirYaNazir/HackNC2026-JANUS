import type { Session } from '@janus/client'
import { generationStatus, messagingLabel, type MessagingStatus } from '../generationStatus'

export function GenerationStatus({ session, messaging = null }: { session: Session | null; messaging?: MessagingStatus | null }) {
  const status = generationStatus(session)
  const label = messagingLabel(messaging)
  return <p className={`generation-status is-${status.tone}`} role="status">{status.text}{status.links.map((link) => <a key={link.href} href={link.href} target="_blank" rel="noreferrer">{link.label}</a>)}{label && <span className="messaging-status">{label}{messaging?.missing?.length ? ` (set ${messaging.missing.join(', ')})` : ''}{!messaging?.connected && <a href="https://github.com/VazirYaNazir/HackNC2026-Janus/blob/main/README.md#text-your-laptop-imessage" target="_blank" rel="noreferrer">iMessage setup</a>}</span>}</p>
}

import type { Session } from '@janus/client'
import { generationStatus } from '../generationStatus'

export function GenerationStatus({ session }: { session: Session | null }) {
  const status = generationStatus(session)
  return <p className={`generation-status is-${status.tone}`} role="status">{status.text}{status.links.map((link) => <a key={link.href} href={link.href} target="_blank" rel="noreferrer">{link.label}</a>)}</p>
}

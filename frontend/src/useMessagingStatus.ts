import { useEffect, useState } from 'react'
import type { PanelClient } from '@janus/client'
import type { MessagingStatus } from './generationStatus'

/** Polls the backend for whether the iMessage sidecar is running (it sends a heartbeat every 15 s). */
export function useMessagingStatus(client: PanelClient | null): MessagingStatus | null {
  const [snapshot, setSnapshot] = useState<{ client: PanelClient; status: MessagingStatus | null } | null>(null)
  useEffect(() => {
    if (!client) return
    const abort = new AbortController()
    let timer: number | undefined
    const poll = async () => {
      try {
        const status = await client.request<MessagingStatus>('/api/v1/studio/messaging', {
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(30_000)]),
        })
        if (!abort.signal.aborted) setSnapshot({ client, status })
      } catch {
        if (!abort.signal.aborted) setSnapshot({ client, status: null })
      }
      // A slow request must finish before the next poll can begin.
      if (!abort.signal.aborted) timer = window.setTimeout(() => { void poll() }, 10_000)
    }
    void poll()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [client])
  return snapshot?.client === client ? snapshot.status : null
}

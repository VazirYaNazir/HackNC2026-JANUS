import { useEffect, useState } from 'react'
import type { PanelClient } from '@janus/client'
import type { MessagingStatus } from './generationStatus'

/** Polls the backend for whether the iMessage sidecar is running (it sends a heartbeat every 15 s). */
export function useMessagingStatus(client: PanelClient | null): MessagingStatus | null {
  const [status, setStatus] = useState<MessagingStatus | null>(null)
  useEffect(() => {
    if (!client) return
    let cancelled = false
    const poll = () => {
      client.request<MessagingStatus>('/api/v1/studio/messaging')
        .then((next) => { if (!cancelled) setStatus(next) })
        .catch(() => { if (!cancelled) setStatus(null) })
    }
    poll()
    const timer = window.setInterval(poll, 10_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [client])
  return client ? status : null
}

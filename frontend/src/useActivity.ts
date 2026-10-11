import { useEffect, useState } from 'react'
import type { ActivityEvent, PanelClient } from '@janus/client'

const REFRESH_MS = 15_000

export interface ActivityFeed { events: ActivityEvent[]; at: number }

/** The last 50 actions (widgets, iMessage, generated programs) when the backend has Tiger Data; otherwise null. `at` is when they were fetched. */
export function useActivity(client: PanelClient | null): ActivityFeed | null {
  const [state, setState] = useState<{ client: PanelClient; feed: ActivityFeed | null } | null>(null)
  useEffect(() => {
    if (!client) return
    let cancelled = false
    const poll = () => {
      client.activity()
        .then((result) => { if (!cancelled) setState({ client, feed: result.available ? { events: result.events, at: Date.now() } : null }) })
        .catch(() => { if (!cancelled) setState({ client, feed: null }) })
    }
    poll()
    const timer = window.setInterval(poll, REFRESH_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [client])
  return state && state.client === client ? state.feed : null
}

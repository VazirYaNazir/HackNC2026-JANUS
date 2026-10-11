import { describeActivity } from '../activity'
import type { ActivityFeed } from '../useActivity'

/** Small collapsible "Recent activity" list. Not rendered at all when there is no Tiger Data history to show. */
export function ActivityPanel({ feed }: { feed: ActivityFeed | null }) {
  if (!feed) return null
  const { events, at: now } = feed
  return <details className="activity-panel">
    <summary>Recent activity{events.length ? ` (${events.length})` : ''}</summary>
    {events.length === 0
      ? <p className="activity-empty">Nothing yet. Actions from widgets, iMessage and generated programs show up here.</p>
      : <ul aria-label="Recent activity">{events.map((event) => {
        const line = describeActivity(event, now)
        return <li key={line.key}><strong>{line.text}</strong><span>{line.source}</span><span className={`activity-${line.tone}`}>{line.outcome}</span><time dateTime={event.time}>{line.when}</time></li>
      })}</ul>}
  </details>
}

import type { ActivityEvent } from '@janus/client'

const SOURCE_LABELS: Record<ActivityEvent['source'], string> = { widget: 'Widget', imessage: 'iMessage', generated: 'Generated' }
const ACTION_LABELS: Record<string, string> = {
  native_volume: 'Volume', native_left_click: 'Left click', native_right_click: 'Right click', native_scroll: 'Scroll',
  lock: 'Lock', status: 'Status', apps: 'Apps', volume_get: 'Volume check', history: 'History',
}

export type OutcomeTone = 'ok' | 'pending' | 'error'

export interface ActivityLine {
  key: string
  text: string
  source: string
  outcome: string
  tone: OutcomeTone
  when: string
}

/** "just now", "5m ago", "3h ago", "2d ago"; a date that is missing or in the future reads as "just now". */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso)
  if (!Number.isFinite(then)) return ''
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 45) return 'just now'
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))}m ago`
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86_400)}d ago`
}

export function toneOf(outcome: string): OutcomeTone {
  if (outcome === 'ok') return 'ok'
  if (outcome.startsWith('error')) return 'error'
  return 'pending'
}

export function describeActivity(event: ActivityEvent, now: number = Date.now()): ActivityLine {
  const label = ACTION_LABELS[event.action_id] ?? event.action_id
  const value = event.args && typeof event.args.value === 'number' ? ` ${Math.round(event.args.value)}` : ''
  const failed = event.outcome.startsWith('error:') ? event.outcome.slice(6).replace(/_/g, ' ').toLowerCase() : ''
  return {
    key: `${event.time}|${event.action_id}|${event.request_id ?? ''}`,
    text: `${label}${value}`,
    source: SOURCE_LABELS[event.source] ?? event.source,
    outcome: failed ? `failed: ${failed}` : event.outcome === 'submitted' ? 'submitted' : event.outcome,
    tone: toneOf(event.outcome),
    when: relativeTime(event.time, now),
  }
}

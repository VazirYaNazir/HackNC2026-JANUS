import { describe, expect, it, vi } from 'vitest'
import type { ActivityEvent } from '@janus/client'
import { PanelClient } from '@janus/client'
import { HISTORY_METRICS, historySources } from '../../shared/client/src/useMetricHistory'
import { sparklinePath } from '../../shared/widgets/src/sparklinePath'
import { describeActivity, relativeTime, toneOf } from '../src/activity'

describe('sparklinePath', () => {
  it('draws oldest-to-newest left to right, scaled into the box with padding', () => {
    expect(sparklinePath([0, 10, 5], 60, 14)).toBe('M0.00 12.00 L30.00 2.00 L60.00 7.00')
  })
  it('puts a flat series in the middle instead of dividing by zero', () => {
    expect(sparklinePath([40, 40, 40], 60, 14)).toBe('M0.00 7.00 L30.00 7.00 L60.00 7.00')
  })
  it('draws nothing for fewer than two usable points', () => {
    for (const values of [[], [5], [Number.NaN, 5], [Number.NaN, Number.POSITIVE_INFINITY]]) expect(sparklinePath(values, 60, 14)).toBe('')
  })
  it('skips non-finite samples but keeps the rest', () => {
    expect(sparklinePath([1, Number.NaN, 3], 10, 10)).toBe('M0.00 8.00 L10.00 2.00')
  })
})

describe('historySources', () => {
  it('keeps only the metrics Tiger records, once each, in a stable order', () => {
    expect(historySources(['mem.percent', 'cpu.percent', 'cpu.percent', 'net.rx', 'volume.percent', 'process.tree'])).toEqual(['cpu.percent', 'mem.percent'])
    expect(historySources([])).toEqual([])
    expect(HISTORY_METRICS).toEqual(['cpu.percent', 'mem.percent', 'mem.used_mb', 'cpu.temperature'])
  })
})

describe('PanelClient history calls', () => {
  it('asks for the metric and range, encoded, and the activity feed', async () => {
    const client = new PanelClient({ host: 'http://127.0.0.1:8000', token: 't' })
    const request = vi.spyOn(client, 'request').mockResolvedValue({ available: false, points: [], events: [] })
    await client.metricHistory('cpu.percent', '1h')
    await client.metricHistory('a b&c', '30m')
    await client.activity()
    expect(request.mock.calls.map((call) => call[0])).toEqual([
      '/api/metrics/history?metric=cpu.percent&range=1h',
      '/api/metrics/history?metric=a%20b%26c&range=30m',
      '/api/activity',
    ])
  })
})

describe('activity text', () => {
  const NOW = Date.parse('2026-10-11T12:00:00Z')
  const event = (over: Partial<ActivityEvent> = {}): ActivityEvent => ({
    time: '2026-10-11T11:55:00Z', source: 'imessage', action_id: 'native_volume', args: { value: 34.6 }, outcome: 'ok', request_id: 'm1', ...over,
  })

  it('formats relative times', () => {
    expect(relativeTime('2026-10-11T11:59:50Z', NOW)).toBe('just now')
    expect(relativeTime('2026-10-11T11:55:00Z', NOW)).toBe('5m ago')
    expect(relativeTime('2026-10-11T09:00:00Z', NOW)).toBe('3h ago')
    expect(relativeTime('2026-10-09T12:00:00Z', NOW)).toBe('2d ago')
    expect(relativeTime('2026-10-11T12:05:00Z', NOW)).toBe('just now')   // clock skew: never "in the future"
    expect(relativeTime('not a date', NOW)).toBe('')
  })

  it('describes a volume change from iMessage', () => {
    expect(describeActivity(event(), NOW)).toMatchObject({ text: 'Volume 35', source: 'iMessage', outcome: 'ok', tone: 'ok', when: '5m ago' })
  })

  it('labels widgets and generated programs and shows failures plainly', () => {
    expect(describeActivity(event({ source: 'widget', action_id: 'native_left_click', args: {} }), NOW)).toMatchObject({ text: 'Left click', source: 'Widget' })
    expect(describeActivity(event({ source: 'generated', action_id: 'compute', args: {}, outcome: 'submitted' }), NOW)).toMatchObject({ text: 'compute', source: 'Generated', outcome: 'submitted', tone: 'pending' })
    expect(describeActivity(event({ action_id: 'lock', args: {}, outcome: 'error:NATIVE_COMMAND_TIMEOUT' }), NOW)).toMatchObject({ outcome: 'failed: native command timeout', tone: 'error' })
  })

  it('classifies outcomes', () => {
    expect([toneOf('ok'), toneOf('submitted'), toneOf('error:X'), toneOf('error')]).toEqual(['ok', 'pending', 'error', 'error'])
  })
})

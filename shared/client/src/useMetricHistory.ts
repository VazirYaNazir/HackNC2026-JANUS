import { useEffect, useMemo, useState } from 'react';
import type { PanelClient } from './api';

/** The readings the backend records in Tiger Data; any other dial source has no history to ask for. */
export const HISTORY_METRICS: readonly string[] = ['cpu.percent', 'mem.percent', 'mem.used_mb', 'cpu.temperature'];
export const HISTORY_REFRESH_MS = 60_000;

/** Distinct, recorded metric names among a layout's dial sources, in a stable order. */
export function historySources(sources: readonly string[]): string[] {
  return [...new Set(sources.filter((source) => HISTORY_METRICS.includes(source)))].sort();
}

/**
 * Last-hour history per dial source (oldest first), for gauges to draw as a sparkline. Empty when the backend has no
 * Tiger Data store or it is unreachable: callers just see no history and render as before. Refreshes once a minute.
 */
export function useMetricHistory(client: PanelClient | null, sources: readonly string[]): Record<string, number[]> {
  const key = historySources(sources).join('|');
  const wanted = useMemo(() => (key ? key.split('|') : []), [key]);
  const [loaded, setLoaded] = useState<{ client: PanelClient; key: string; history: Record<string, number[]> } | null>(null);

  useEffect(() => {
    if (!client || wanted.length === 0) return;
    const abort = new AbortController();
    const load = async () => {
      const entries = await Promise.all(wanted.map(async (metric) => {
        try {
          const result = await client.metricHistory(metric, '1h', abort.signal);
          return [metric, result.available ? result.points.map((point) => point.avg).filter(Number.isFinite) : []] as const;
        } catch {
          return [metric, []] as const;
        }
      }));
      if (!abort.signal.aborted) setLoaded({ client, key, history: Object.fromEntries(entries.filter(([, values]) => values.length >= 2)) });
    };
    void load();
    const timer = window.setInterval(() => void load(), HISTORY_REFRESH_MS);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [client, key, wanted]);

  return loaded && loaded.client === client && loaded.key === key ? loaded.history : EMPTY;
}

const EMPTY: Record<string, number[]> = {};

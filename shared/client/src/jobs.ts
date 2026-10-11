import type { JobView } from '@janus/schema';
import { activeJob, jobText } from './api';

/** Latest request state per widget, shared by Studio preview and the phone panel. */
export function widgetStates(jobs: JobView[]): Record<string, string> {
  const states: Record<string, string> = {};
  for (const job of jobs) {
    if (job.widget_id && states[job.widget_id] === undefined) states[job.widget_id] = activeJob(job) ? 'running' : job.state === 'COMPLETED' ? jobText(job) : 'error';
  }
  return states;
}

// HTTP responses, history snapshots and socket updates can arrive out of order.
export function newerJob(current: JobView | undefined, incoming: JobView): JobView {
  if (!current) return incoming;
  if (!activeJob(current) && activeJob(incoming)) return current;
  if (current.elapsed_ms > incoming.elapsed_ms) return current;
  return incoming;
}

export function recordJob(current: JobView[], incoming: JobView): JobView[] {
  const previous = current.find((job) => job.job_id === incoming.job_id);
  return [newerJob(previous, incoming), ...current.filter((job) => job.job_id !== incoming.job_id)].slice(0, 50);
}

export function mergeHistory(current: JobView[], history: JobView[]): JobView[] {
  const existing = new Map(current.map((job) => [job.job_id, job]));
  const received = new Set(history.map((job) => job.job_id));
  return [...current.filter((job) => !received.has(job.job_id)), ...history.map((job) => newerJob(existing.get(job.job_id), job))].slice(0, 50);
}

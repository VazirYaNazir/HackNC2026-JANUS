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

/** Detailed failures stay separate from run state, so every control keeps its failure indicator. */
export function widgetErrors(jobs: JobView[]): Record<string, string> {
  const errors: Record<string, string> = {};
  const seen = new Set<string>();
  for (const job of jobs) {
    if (!job.widget_id || seen.has(job.widget_id)) continue;
    seen.add(job.widget_id);
    if (!activeJob(job) && job.state !== 'COMPLETED') errors[job.widget_id] = job.reason || job.result?.reason || jobText(job);
  }
  return errors;
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

import type { JobView } from '@janus/schema';
import { activeJob } from './api';

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

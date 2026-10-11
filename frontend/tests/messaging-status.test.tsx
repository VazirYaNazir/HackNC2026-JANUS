// @vitest-environment jsdom
import { StrictMode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient } from '@janus/client';
import { useMessagingStatus } from '../src/useMessagingStatus';
import type { MessagingStatus } from '../src/generationStatus';
import './dom';

const connected: MessagingStatus = { enabled: true, connected: true };
const waiting: MessagingStatus = { enabled: true, connected: false, reason: 'SIDECAR_NOT_CONNECTED' };
const client = (name: string) => new PanelClient({ host: 'http://localhost:8000', token: name });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe('messaging connection status lifecycle', () => {
  it('does not request status without a backend', () => {
    const request = vi.spyOn(PanelClient.prototype, 'request');
    const hook = renderHook(() => useMessagingStatus(null));
    expect(hook.result.current).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it('hides the previous laptop status immediately and aborts its request when the client changes', async () => {
    const first = client('first'), second = client('second');
    const next = deferred<MessagingStatus>();
    const firstRequest = vi.spyOn(first, 'request').mockResolvedValue(connected);
    const secondRequest = vi.spyOn(second, 'request').mockReturnValue(next.promise);
    const hook = renderHook(({ backend }: { backend: PanelClient | null }) => useMessagingStatus(backend), { initialProps: { backend: first as PanelClient | null } });
    await act(async () => {});
    expect(hook.result.current).toEqual(connected);
    const firstSignal = firstRequest.mock.calls[0][1]!.signal!;
    hook.rerender({ backend: second });
    expect(hook.result.current).toBeNull();
    expect(firstSignal.aborted).toBe(true);
    expect(secondRequest).toHaveBeenCalledOnce();
    await act(async () => { next.resolve(waiting); });
    expect(hook.result.current).toEqual(waiting);
    hook.rerender({ backend: null });
    expect(hook.result.current).toBeNull();
    expect(secondRequest.mock.calls[0][1]!.signal!.aborted).toBe(true);
  });

  it('ignores a late response from the previous client even when the transport ignores cancellation', async () => {
    vi.useFakeTimers();
    const first = client('first'), second = client('second');
    const previous = deferred<MessagingStatus>();
    const firstRequest = vi.spyOn(first, 'request').mockReturnValue(previous.promise);
    const secondRequest = vi.spyOn(second, 'request').mockResolvedValue(waiting);
    const hook = renderHook(({ backend }) => useMessagingStatus(backend), { initialProps: { backend: first } });
    hook.rerender({ backend: second });
    await act(async () => {});
    expect(hook.result.current).toEqual(waiting);
    await act(async () => { previous.resolve(connected); });
    expect(hook.result.current).toEqual(waiting);
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(firstRequest).toHaveBeenCalledOnce();
    expect(secondRequest).toHaveBeenCalledTimes(2);
  });

  it('waits for a slow request to finish before scheduling another poll', async () => {
    vi.useFakeTimers();
    const backend = client('slow'), pending = deferred<MessagingStatus>();
    const request = vi.spyOn(backend, 'request').mockReturnValueOnce(pending.promise).mockResolvedValue(waiting);
    const hook = renderHook(() => useMessagingStatus(backend));
    await act(async () => { vi.advanceTimersByTime(40_000); });
    expect(request).toHaveBeenCalledOnce();
    expect(hook.result.current).toBeNull();
    await act(async () => { pending.resolve(connected); });
    expect(hook.result.current).toEqual(connected);
    await act(async () => { vi.advanceTimersByTime(9_999); });
    expect(request).toHaveBeenCalledOnce();
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(request).toHaveBeenCalledTimes(2);
    expect(hook.result.current).toEqual(waiting);
  });

  it('clears an unreachable status and resumes polling after failure', async () => {
    vi.useFakeTimers();
    const backend = client('recovering');
    const request = vi.spyOn(backend, 'request').mockResolvedValueOnce(connected).mockRejectedValueOnce(new Error('Backend unavailable')).mockResolvedValue(waiting);
    const hook = renderHook(() => useMessagingStatus(backend));
    await act(async () => {});
    expect(hook.result.current).toEqual(connected);
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(hook.result.current).toBeNull();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(request).toHaveBeenCalledTimes(3);
    expect(hook.result.current).toEqual(waiting);
  });

  it('preserves the HTTP deadline while forwarding cancellation and stops polling after unmount', async () => {
    vi.useFakeTimers();
    const deadline = new AbortController();
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(deadline.signal);
    const backend = client('cancelled'), pending = deferred<MessagingStatus>();
    const request = vi.spyOn(backend, 'request').mockReturnValue(pending.promise);
    const hook = renderHook(() => useMessagingStatus(backend));
    expect(timeout).toHaveBeenCalledWith(30_000);
    expect(request.mock.calls[0][0]).toBe('/api/v1/studio/messaging');
    const signal = request.mock.calls[0][1]!.signal!;
    expect(signal.aborted).toBe(false);
    deadline.abort();
    expect(signal.aborted).toBe(true);
    hook.unmount();
    await act(async () => { pending.reject(new Error('Cancelled')); vi.advanceTimersByTime(60_000); });
    expect(request).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ignores the cancelled Strict Mode probe and retains the active result', async () => {
    const backend = client('strict'), cancelled = deferred<MessagingStatus>();
    const request = vi.spyOn(backend, 'request').mockReturnValueOnce(cancelled.promise).mockResolvedValue(waiting);
    const hook = renderHook(() => useMessagingStatus(backend), { wrapper: StrictMode });
    await act(async () => {});
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][1]!.signal!.aborted).toBe(true);
    expect(hook.result.current).toEqual(waiting);
    await act(async () => { cancelled.resolve(connected); });
    expect(hook.result.current).toEqual(waiting);
  });
});

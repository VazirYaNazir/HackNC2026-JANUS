// @vitest-environment jsdom
import { StrictMode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient, usePanel, useNativeControls, NativeControls, ApiError } from '@janus/client';
import type { ServerMessage } from '@janus/schema';
import { createEmptyLayout } from '../src/editor/layout';
import { laptopProject } from '../src/editor/project';
import './dom';

function fixture() {
  const client = new PanelClient({ host: 'http://localhost:8000', token: 'fixture' });
  const layout = vi.spyOn(client, 'layout').mockResolvedValue({ layout: createEmptyLayout(), revision: 1 });
  const history = vi.spyOn(client, 'history').mockResolvedValue({ jobs: [] });
  const session = vi.spyOn(client, 'session').mockResolvedValue({ device_id: 'laptop', online: true, target: null });
  let message!: (message: ServerMessage) => void, status!: (value: string) => void;
  const close = vi.fn();
  vi.spyOn(client, 'subscribe').mockImplementation((m, s) => { message = m; status = s; return close; });
  return { client, layout, history, session, close, message: (m: ServerMessage) => act(() => message(m)), status: (s: string) => act(() => status(s)) };
}

describe('live panel hook', () => {
  it('loads independently, orders revisions, bounds sources and clears stale readings when the laptop disconnects', async () => {
    const f = fixture(), hook = renderHook(() => usePanel(f.client));
    f.status('connected'); f.message({ type: 'auth_ok' }); await waitFor(() => expect(hook.result.current.document?.revision).toBe(1));
    f.message({ type: 'data', source: 'mem.percent', value: 23 }); expect(hook.result.current.readings['mem.percent']).toBe(23);
    f.message({ type: 'data', source: 'mem.percent', value: null }); expect(hook.result.current.readings['mem.percent']).toBeNull();
    act(() => hook.result.current.setDocument({ layout: createEmptyLayout('new'), revision: 4 }));
    f.message({ type: 'layout_updated' }); await waitFor(() => expect(f.layout).toHaveBeenCalledTimes(2)); expect(hook.result.current.document?.revision).toBe(4);
    for (let i = 0; i < 260; i++) f.message({ type: 'data', source: `source-${i}`, value: i });
    expect(Object.keys(hook.result.current.readings)).toHaveLength(256);
    f.message({ type: 'agent_presence', session: { device_id: 'laptop', online: false, target: null } }); expect(hook.result.current.readings).toEqual({});
    f.message({ type: 'data', source: 'screen.frame', value: 'frame' }); f.status('disconnected'); expect(hook.result.current.readings).toEqual({});
    hook.unmount(); expect(f.close).toHaveBeenCalled();
  });
  it('preserves session/history despite invalid layout, recovers valid updates and reports revocation', async () => {
    const f = fixture(); f.layout.mockRejectedValueOnce(new Error('invalid geometry'));
    const hook = renderHook(() => usePanel(f.client)); f.message({ type: 'auth_ok' });
    await waitFor(() => expect(hook.result.current.error).toBe('invalid geometry')); expect(hook.result.current.session?.online).toBe(true);
    f.message({ type: 'layout_updated' }); await waitFor(() => expect(hook.result.current.error).toBe(''));
    f.session.mockRejectedValueOnce(new ApiError('revoked', 401)); f.message({ type: 'auth_ok' });
    await waitFor(() => expect(hook.result.current.status).toBe('unauthorized'));
  });
  it('ignores late results and prevents another laptop inheriting readings', async () => {
    const first = fixture(), second = fixture();
    let resolve!: (value: { layout: ReturnType<typeof createEmptyLayout>; revision: number }) => void;
    first.layout.mockReturnValue(new Promise(r => { resolve = r; }));
    const hook = renderHook(({ client }) => usePanel(client), { initialProps: { client: first.client } });
    first.message({ type: 'auth_ok' }); first.message({ type: 'data', source: 'cpu', value: 80 });
    hook.rerender({ client: second.client }); expect(hook.result.current.readings).toEqual({});
    await act(async () => resolve({ layout: createEmptyLayout('old laptop'), revision: 99 })); expect(hook.result.current.document).toBeNull();
    expect(first.close).toHaveBeenCalled();
  });
});

describe('native controls', () => {
  it('preserves command order and coalesces motion so the final zero is acknowledged last', async () => {
    const f = fixture(), error = vi.fn(), controls = new NativeControls(f.client, error);
    const sent: unknown[] = []; let release!: () => void;
    vi.spyOn(f.client, 'native').mockImplementation(async payload => { sent.push(payload.value); if (sent.length === 1) await new Promise<void>(r => { release = r; }); return { ok: true, request_id: 'r' }; });
    const w = laptopProject().widgets[1]; if (!('action' in w)) throw new Error('input expected');
    controls.send(w, { x: 1, y: 0 }); controls.send(w, { x: .5, y: 0 }); controls.send(w, { x: 0, y: 0 });
    expect(sent).toEqual([{ x: 1, y: 0 }]); release(); await controls.flush(); expect(sent).toEqual([{ x: 1, y: 0 }, { x: 0, y: 0 }]); expect(error).not.toHaveBeenCalled();
  });
  it('runs separate physical controls concurrently, reports failures and stops pending work on close', async () => {
    const f = fixture(), error = vi.fn(), controls = new NativeControls(f.client, error);
    vi.spyOn(f.client, 'native').mockRejectedValue(new Error('permission denied'));
    for (const w of laptopProject().widgets.slice(0, 2)) if ('action' in w) controls.send(w, w.type === 'slider' ? 40 : { x: 0, y: 0 });
    await controls.flush(); expect(f.client.native).toHaveBeenCalledTimes(2); expect(error).toHaveBeenCalledWith('permission denied');
    controls.close(); const w = laptopProject().widgets[0]; if ('action' in w) controls.send(w, 50); expect(f.client.native).toHaveBeenCalledTimes(2);
  });
  it('survives React StrictMode effect cleanup and recreation', async () => {
    const f = fixture(); vi.spyOn(f.client, 'native').mockResolvedValue({ ok: true, request_id: 'r' });
    const hook = renderHook(() => useNativeControls(f.client, vi.fn()), { wrapper: StrictMode });
    const w = laptopProject().widgets[0]; if ('action' in w) act(() => hook.result.current.send(w, 20));
    await waitFor(() => expect(f.client.native).toHaveBeenCalledTimes(1)); hook.unmount();
  });
});

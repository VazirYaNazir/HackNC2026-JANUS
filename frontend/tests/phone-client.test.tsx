// @vitest-environment jsdom
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient } from '@janus/client';
import { DemoClient, DEMO_HOST, createClient, demoLayout, isDemoPanel } from '../../PWA/src/lib/demo';
import { checkHost, pair } from '../../PWA/src/lib/connection';
import Gallery from '../../PWA/src/screens/Gallery';
import './dom';

describe('phone connection facade', () => {
  it('checks only authenticated laptop status, treats demo as local and reports network failures', async () => {
    const session = vi.spyOn(PanelClient.prototype, 'session').mockResolvedValueOnce({ device_id: 'laptop', online: true, target: null }).mockRejectedValueOnce(new Error('offline'));
    expect(await checkHost(DEMO_HOST, 'demo')).toBe(true); expect(session).not.toHaveBeenCalled();
    expect(await checkHost('https://backend.example', 'phone')).toBe(true); expect(await checkHost('https://backend.example', 'phone')).toBe(false);
    expect(await checkHost('http://untrusted.example', 'phone')).toBe(false);
  });
  it('exchanges the phone code through the shared client and forwards abort signals', async () => {
    const result = { token: 'phone', deviceId: 'phone-id', agentDeviceId: 'laptop', pcName: 'PC' };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), { status: 200 })); vi.stubGlobal('fetch', fetch);
    expect(await pair('https://backend.example', 'ABCDEF123456', new AbortController().signal)).toEqual(result);
    expect(fetch.mock.calls[0][1].body).toBe(JSON.stringify({ code: 'ABCDEF123456', name: 'Mobile phone' }));
  });
});

describe('demo mode', () => {
  it('uses no network and presents a complete layout, target and request preparation', async () => {
    const demo = new DemoClient(); expect((await demo.layout()).layout).toEqual(demoLayout); expect((await demo.session()).online).toBe(true);
    await expect(demo.request('/any')).rejects.toThrow(/no network/);
    const request = await demo.prepare({ widget_id: demoLayout.widgets[0].id, job_id: 'r' }); expect(request.checked).toBe(true); expect(request.matches).toEqual([]);
    const panel = { id: 'demo', name: 'Demo', pcName: 'PC', host: DEMO_HOST, token: 'demo', addedAt: 1 };
    expect(isDemoPanel(panel)).toBe(true); expect(createClient(panel)).toBeInstanceOf(DemoClient); expect(createClient({ ...panel, host: 'https://backend.example', id: 'real' })).toBeInstanceOf(PanelClient);
  });
  it('emits live simulated readings and typed results for every input; cancellation prevents later completion', async () => {
    vi.useFakeTimers(); const demo = new DemoClient(), receive = vi.fn(), status = vi.fn(), close = demo.subscribe(receive, status);
    act(() => vi.advanceTimersByTime(1000)); expect(status).toHaveBeenCalledWith('connected'); expect(receive).toHaveBeenCalledWith(expect.objectContaining({ type: 'data', source: 'cpu.percent' }));
    const inputs = demoLayout.widgets.filter(w => 'action' in w);
    for (const w of inputs) await demo.dispatch({ widget_id: w.id, job_id: w.id, value: w.type === 'button' ? undefined : w.type === 'joystick' ? { x: 0, y: 1 } : w.type === 'toggle' ? true : w.type === 'agent' || w.type === 'command' ? 'hello' : 5 });
    await demo.dispatch({ widget_id: 'unknown', job_id: 'unknown' });
    await demo.dispatch({ widget_id: inputs[0].id, job_id: 'cancel' }); await demo.cancel('cancel');
    act(() => vi.advanceTimersByTime(1500)); const history = await demo.history(); expect(history.jobs.every(job => job.state === 'COMPLETED')).toBe(true);
    expect(receive.mock.calls.filter(([message]) => message.type === 'job_update' && message.job.job_id === 'cancel')).toHaveLength(0);
    expect(history.jobs).toHaveLength(inputs.length + 1); close(); const count = receive.mock.calls.length; act(() => vi.advanceTimersByTime(5000)); expect(receive).toHaveBeenCalledTimes(count);
  });
  it('renders the developer gallery with all built-in widget families', () => {
    render(<Gallery />); expect(screen.getByRole('heading', { name: /gallery/i })).toBeTruthy(); expect(document.querySelectorAll('.jw').length).toBeGreaterThan(17);
  });
});

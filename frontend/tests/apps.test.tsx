// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import { PanelClient } from '@janus/client';
import type { ServerMessage } from '@janus/schema';
import { createWidget } from '@janus/widgets';
import Studio from '../src/App';
import PWA from '../../PWA/src/App';
import ScanScreen from '../../PWA/src/screens/ScanScreen';
import PanelScreen from '../../PWA/src/screens/PanelScreen';
import { laptopProject } from '../src/editor/project';
import * as connection from '../../PWA/src/lib/connection';
import { addPanel, listPanels } from '../../PWA/src/lib/panels';
import './dom';

vi.mock('html5-qrcode', () => ({ Html5Qrcode: class { start = vi.fn().mockRejectedValue(new Error('camera denied')); stop = vi.fn().mockResolvedValue(undefined); clear = vi.fn(); } }));

function backend(project = laptopProject()) {
  let layout = project, revision = 1, receive!: (message: ServerMessage) => void;
  vi.spyOn(PanelClient.prototype, 'request').mockImplementation(async path => {
    if (path === '/api/v1/studio/session') return { device_id: 'laptop', online: true };
    if (path === '/api/layout') return { layout, revision };
    if (path === '/api/v1/agent-jobs') return { jobs: [] };
    if (path === '/api/v1/panels/session') return { device_id: 'laptop', online: true, target: null };
    throw new Error('Unexpected API path');
  });
  vi.spyOn(PanelClient.prototype, 'save').mockImplementation(async next => { layout = next as ReturnType<typeof laptopProject>; revision++; return { layout, revision }; });
  vi.spyOn(PanelClient.prototype, 'native').mockResolvedValue({ ok: true, request_id: 'r' });
  vi.spyOn(PanelClient.prototype, 'subscribe').mockImplementation((onMessage, onStatus) => {
    receive = onMessage; const timer = setTimeout(() => { onStatus('connected'); onMessage({ type: 'auth_ok' }); }, 0); return () => clearTimeout(timer);
  });
  return { message: (message: ServerMessage) => act(() => receive(message)) };
}

describe('both app roots and panel workflows', () => {
  it('Studio consumes launcher bootstrap only once in StrictMode and keeps credentials out of URL and storage', async () => {
    backend(); localStorage.clear();
    window.history.replaceState(null, '', '/#janus-start=http%3A%2F%2F127.0.0.1%3A12345&nonce=fixture_nonce_1234567890');
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ host: 'http://localhost:8000', token: 'startup-controller-secret' }) });
    vi.stubGlobal('fetch', fetch);
    render(<StrictMode><Studio /></StrictMode>);
    await waitFor(() => expect(screen.getByRole('button', { name: /Laptop online · connected/ })).toBeTruthy());
    expect(fetch).toHaveBeenCalledTimes(1); expect(window.location.hash).toBe('');
    expect(document.body.textContent).not.toContain('startup-controller-secret'); expect(localStorage.length).toBe(0);
  });

  it('Studio mirrors native permission errors and generated Agent replies just as the phone does', async () => {
    const project = laptopProject();
    project.widgets = [{ ...createWidget('agent', 'assistant', { x: 0, y: 0, w: 360, h: 170 }), action: 'assistant_action' } as typeof project.widgets[number], { ...project.widgets[7], y: 182 }];
    const b = backend(project), view = render(<Studio />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect backend' }));
    fireEvent.change(screen.getByLabelText('Laptop controller credential'), { target: { value: 'controller' } }); fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Laptop online · connected/ })).toBeTruthy());
    b.message({ type: 'data', source: 'native.screen.status', value: 'Screen Recording permission denied' });
    expect(screen.getByText('Screen Recording permission denied')).toBeTruthy();
    const job = { job_id: 'generated', device_id: 'laptop', widget_id: 'assistant', state: 'GENERATING', reason: null, result: null, elapsed_ms: 1, cancellation_confirmed: false };
    b.message({ type: 'job_update', job }); expect(screen.getByText('Thinking…')).toBeTruthy();
    b.message({ type: 'job_update', job: { ...job, state: 'COMPLETED', result: { stdout: 'Reply from laptop' }, elapsed_ms: 2 } });
    expect(screen.getByText('Reply from laptop')).toBeTruthy();
    b.message({ type: 'data', source: 'native.screen.status', value: 'Live' });
    b.message({ type: 'data', source: 'screen.frame', value: 'data:image/jpeg;base64,frame' });
    fireEvent.load(view.container.querySelector('.jw--stream img')!); expect(screen.getByText('LIVE')).toBeTruthy();
  });
  it('Studio connects, publishes, previews live data, dispatches native controls and opens history', async () => {
    const b = backend(); render(<Studio />); fireEvent.click(screen.getByRole('button', { name: 'Connect backend' }));
    fireEvent.change(screen.getByLabelText('Laptop controller credential'), { target: { value: 'controller' } }); fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Laptop online · connected/ })).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Office' } }); fireEvent.blur(screen.getByLabelText('Project name'));
    fireEvent.click(screen.getByRole('button', { name: 'Save & Push' })); await waitFor(() => expect(PanelClient.prototype.save).toHaveBeenCalledWith(expect.objectContaining({ name: 'Office' }), 1));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Dismiss' })).toBeTruthy()); fireEvent.click(screen.getByRole('button', { name: 'Dismiss' })); expect(screen.queryByText('Panel saved and pushed to paired phones.')).toBeNull();
    b.message({ type: 'data', source: 'volume.percent', value: 71 }); fireEvent.click(screen.getByRole('button', { name: 'Edit', exact: true }));
    expect((screen.getByRole('slider', { name: 'Volume' }) as HTMLInputElement).value).toBe('71');
    fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true })); await waitFor(() => expect(PanelClient.prototype.native).toHaveBeenCalledWith(expect.objectContaining({ widget_id: 'left-click' })));
    fireEvent.click(screen.getByRole('button', { name: 'Requests', exact: true })); expect(screen.getByRole('dialog', { name: 'Recent requests' })).toBeTruthy();
  });
  it('phone panels dispatch built-in actions and block offline controls', async () => {
    const b = backend(), panel = { id: 'p', name: 'Phone', pcName: 'Laptop', host: 'http://localhost:8000', token: 'phone', addedAt: 1 };
    localStorage.clear(); render(<PanelScreen panel={panel} onBack={() => {}} onRepair={() => {}} />);
    await waitFor(() => expect(screen.getByText('Laptop online · connected')).toBeTruthy());
    b.message({ type: 'data', source: 'volume.percent', value: 30 }); expect((screen.getByRole('slider', { name: 'Volume' }) as HTMLInputElement).value).toBe('30');
    fireEvent.click(screen.getByRole('button', { name: 'Right click', exact: true })); await waitFor(() => expect(PanelClient.prototype.native).toHaveBeenCalledWith(expect.objectContaining({ widget_id: 'right-click' })));
    b.message({ type: 'agent_presence', session: { device_id: 'laptop', online: false, target: null } });
    fireEvent.click(screen.getByRole('button', { name: 'Right click is unavailable right now' })); expect(screen.getByText('Your PC is offline.')).toBeTruthy();
  });
  it('manual pairing persists its credential immediately and saves the panel name', async () => {
    localStorage.clear(); const done = vi.fn(); vi.spyOn(connection, 'pair').mockResolvedValue({ token: 'phone', deviceId: 'phone-id', agentDeviceId: 'laptop', pcName: 'Laptop' });
    render(<ScanScreen method="manual" onDone={done} />);
    fireEvent.change(screen.getByLabelText('PC address'), { target: { value: 'https://backend.example' } }); fireEvent.change(screen.getByLabelText('Pairing code'), { target: { value: 'ABCDEF123456' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pair', exact: true })); await waitFor(() => expect(screen.getByLabelText('Panel name')).toBeTruthy());
    expect(listPanels()[0].token).toBe('phone'); fireEvent.change(screen.getByLabelText('Panel name'), { target: { value: 'Office' } }); fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true })); expect(listPanels()[0].name).toBe('Office'); expect(done).toHaveBeenCalled();
  });
  it('failed pairing retains details for retry; camera denial has manual fallback', async () => {
    vi.spyOn(connection, 'pair').mockRejectedValue(new Error('Expired code')); const view = render(<ScanScreen method="manual" onDone={() => {}} />);
    fireEvent.change(screen.getByLabelText('PC address'), { target: { value: 'https://backend.example' } }); fireEvent.change(screen.getByLabelText('Pairing code'), { target: { value: 'ABCDEF123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Pair', exact: true }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Expired code')); expect((screen.getByLabelText('Pairing code') as HTMLInputElement).value).toBe('ABCDEF123456');
    view.unmount(); render(<ScanScreen onDone={() => {}} />); await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy()); expect(screen.getByRole('button', { name: 'Enter code instead' })).toBeTruthy();
  });
  it('the PWA root navigates saved panels and opens manual pairing', async () => {
    backend(); localStorage.clear(); window.history.replaceState({}, '', '/'); vi.spyOn(connection, 'checkHost').mockResolvedValue(true);
    addPanel({ name: 'Desk', pcName: 'Laptop', host: 'http://localhost:8000', token: 'phone' }); render(<PWA />);
    fireEvent.click(screen.getByText('Desk', { exact: true })); await waitFor(() => expect(screen.getByRole('button', { name: 'Back to panels' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Back to panels' })); fireEvent.click(screen.getByRole('button', { name: 'Enter code instead' })); await waitFor(() => expect(screen.getByLabelText('Pairing code')).toBeTruthy());
  });
});

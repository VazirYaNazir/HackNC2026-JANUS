// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient } from '@janus/client';
import { BottomBar } from '../src/components/BottomBar';
import { ConnectionDialog } from '../src/components/ConnectionDialog';
import { PairingDialog } from '../src/components/PairingDialog';
import { WidgetPalette } from '../src/components/WidgetPalette';
import { CanvasToolbar } from '../src/components/CanvasToolbar';
import { WIDGET_TYPES } from '@janus/widgets';
import './dom';

vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,fixture') } }));

describe('frontend forms and toolbars', () => {
  it('prepares the requested platform, filters incompatible libraries, and preserves explicit capabilities', async () => {
    const generate = vi.fn().mockResolvedValue(undefined); render(<BottomBar enabled target={null} onGenerate={generate} />);
    expect(screen.getByRole('button', { name: 'Generate', exact: true }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('AI assistant prompt'), { target: { value: '  set volume  ' } });
    fireEvent.click(screen.getByLabelText('coreaudio')); fireEvent.click(screen.getByLabelText('audio.volume'));
    fireEvent.change(screen.getByLabelText('Platform'), { target: { value: 'Windows' } }); fireEvent.click(screen.getByLabelText('win32'));
    fireEvent.submit(screen.getByRole('form', { name: 'AI assistant toolbar' }));
    await waitFor(() => expect(generate).toHaveBeenCalledWith(expect.objectContaining({ description: 'set volume', platform: 'Windows', dependencies: ['win32'], capabilities: ['audio.volume'] })));
  });
  it('uses the connected agent target and disables Generate while an operation is in progress', async () => {
    let finish!: () => void; const generate = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    render(<BottomBar enabled target={{ platform: 'Windows', architecture: 'x86_64', build_boundary_available: false, execution_boundary_available: false }} onGenerate={generate} />);
    expect(screen.queryByLabelText('Platform')).toBeNull(); fireEvent.change(screen.getByLabelText('AI assistant prompt'), { target: { value: 'compute' } }); fireEvent.click(screen.getByRole('button', { name: 'Generate', exact: true }));
    expect(screen.getByRole('button', { name: 'Preparing…' }).hasAttribute('disabled')).toBe(true);
    await act(async () => finish()); expect(generate.mock.calls[0][0]).toMatchObject({ platform: 'Windows', architecture: 'x86_64' });
  });
  it('authenticates a Studio connection before loading it and displays server errors without dropping inputs', async () => {
    const connect = vi.fn().mockResolvedValue(undefined), close = vi.fn(), request = vi.spyOn(PanelClient.prototype, 'request').mockRejectedValueOnce(new Error('UNAUTHORIZED')).mockResolvedValue({});
    render(<ConnectionDialog onConnect={connect} onClose={close} />);
    fireEvent.change(screen.getByLabelText('Backend address'), { target: { value: 'https://backend.example' } }); fireEvent.change(screen.getByLabelText('Laptop controller credential'), { target: { value: 'controller-fixture' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true })); await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('UNAUTHORIZED')); expect(connect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true })); await waitFor(() => expect(connect).toHaveBeenCalled()); expect(request).toHaveBeenCalledWith('/api/v1/studio/session'); expect(close).toHaveBeenCalled();
  });
  it('shows backend pairing codes, phone links, expiry and fresh QR codes', async () => {
    vi.useFakeTimers(); const client = new PanelClient({ host: 'http://localhost:8000', token: 'fixture' });
    const pairing = vi.spyOn(client, 'pairing').mockResolvedValue({ code: 'ABCDEF123456', host: 'https://backend.example', expires_in_seconds: 2, pwa_url: 'https://pwa.example' });
    render(<PairingDialog client={client} onClose={() => {}} />); await act(async () => {});
    expect(screen.getByRole('img', { name: 'JANUS pairing QR code' })).toBeTruthy(); expect(screen.getByText('ABCDEF123456')).toBeTruthy();
    expect((screen.getByLabelText('Pairing link') as HTMLInputElement).value).toContain('https://pwa.example');
    act(() => vi.advanceTimersByTime(2500)); expect(screen.queryByRole('img', { name: 'JANUS pairing QR code' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'New code' })); await act(async () => {}); expect(pairing).toHaveBeenCalledTimes(2);
  });
  it('lists every control and display and disables dragging in preview', () => {
    const view = render(<WidgetPalette onDragStart={() => {}} onDragEnd={() => {}} disabled={false} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(WIDGET_TYPES.length);
    view.rerender(<WidgetPalette onDragStart={() => {}} onDragEnd={() => {}} disabled />); expect(screen.getAllByRole('listitem').every(item => item.getAttribute('draggable') === 'false')).toBe(true);
  });
  it('dispatches all canvas toolbar actions and enforces preview state', () => {
    const snap = vi.fn(), mode = vi.fn(), undo = vi.fn(), redo = vi.fn(); const view = render(<CanvasToolbar snapToGrid={false} mode="edit" canUndo canRedo onToggleSnap={snap} onToggleMode={mode} onUndo={undo} onRedo={redo} />);
    fireEvent.click(screen.getByRole('button', { name: 'Free' })); fireEvent.click(screen.getByRole('button', { name: 'Edit' })); fireEvent.click(screen.getByRole('button', { name: 'Undo' })); fireEvent.click(screen.getByRole('button', { name: 'Redo' }));
    for (const fn of [snap, mode, undo, redo]) expect(fn).toHaveBeenCalledTimes(1);
    view.rerender(<CanvasToolbar snapToGrid mode="preview" canUndo canRedo onToggleSnap={snap} onToggleMode={mode} onUndo={undo} onRedo={redo} />); expect(screen.getByRole('button', { name: 'Undo' }).hasAttribute('disabled')).toBe(true);
  });
});

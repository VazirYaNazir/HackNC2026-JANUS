// @vitest-environment jsdom
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import HomeScreen from '../../PWA/src/screens/HomeScreen';
import InstallBanner from '../../PWA/src/components/InstallBanner';
import Dialog from '../../PWA/src/components/Dialog';
import { addDemoPanel, addPanel, listPanels, removePanel, renamePanel, repairPanel, seedDemoPanel, touchPanel } from '../../PWA/src/lib/panels';
import { dismissInstall, isDismissed, isIos, isStandalone, promptInstall, useInstallKind } from '../../PWA/src/lib/install';
import * as connection from '../../PWA/src/lib/connection';
import './dom';

beforeEach(() => { localStorage.clear(); fireEvent(window, new Event('appinstalled')); });
const saved = { name: 'Desk', pcName: 'Laptop', host: 'https://backend.example', token: 'phone-fixture', agentDeviceId: 'laptop' };

describe('phone panel storage and home screen', () => {
  it('ignores malformed entries, wrong shapes and duplicate IDs', () => {
    for (const raw of ['bad', '{}', 'null', '[null,1,{}]']) { localStorage.setItem('janus.panels', raw); expect(listPanels()).toEqual([]); }
    const p = { ...saved, id: 'p', addedAt: 1 }; localStorage.setItem('janus.panels', JSON.stringify([p, p, { ...p, id: 'bad', token: 1 }])); expect(listPanels()).toEqual([p]);
  });
  it('adds, renames, touches, repairs and removes credentials and cached layout', () => {
    const p = addPanel(saved)[0]; expect(listPanels()).toEqual([p]);
    expect(renamePanel(p.id, 'Office')[0].name).toBe('Office'); expect(touchPanel(p.id)[0].lastOpenedAt).toBeGreaterThan(0);
    const repaired = repairPanel(p.id, { ...saved, host: 'https://new.example', token: 'new-token' })[0]; expect(repaired.id).toBe(p.id); expect(repaired.name).toBe('Office'); expect(repaired.token).toBe('new-token');
    localStorage.setItem(`janus.layout.${p.id}`, '{}'); expect(removePanel(p.id)).toEqual([]);
  });
  it('seeds a demo exactly once and does not restore it after removal', () => {
    seedDemoPanel(); seedDemoPanel(); addDemoPanel(); expect(listPanels()).toHaveLength(1);
    removePanel('demo'); seedDemoPanel(); expect(listPanels()).toHaveLength(0);
  });
  it('opens panels and supports rename, repair, remove and both pairing entry points', async () => {
    const p = addPanel(saved)[0], open = vi.fn(), scan = vi.fn(), manual = vi.fn(), repair = vi.fn(); vi.spyOn(connection, 'checkHost').mockResolvedValue(true);
    render(<HomeScreen onOpen={open} onScan={scan} onManual={manual} onRepair={repair} />);
    await waitFor(() => expect(screen.getByRole('img', { name: 'online' })).toBeTruthy());
    expect(screen.getByRole('heading', { name: 'Janus' }).querySelector('img')?.getAttribute('src')).toBe('/janus-logo.svg');
    fireEvent.click(screen.getByText('Desk')); expect(open).toHaveBeenCalledWith(p);
    fireEvent.click(screen.getByRole('button', { name: 'Options for Desk' })); fireEvent.click(screen.getByRole('button', { name: 'Rename', exact: true }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Office' } }); fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true })); expect(screen.getByText('Office')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Options for Office' })); fireEvent.click(screen.getByRole('button', { name: 'Pair again' })); expect(repair).toHaveBeenCalledWith(expect.objectContaining({ id: p.id, name: 'Office' }));
    fireEvent.click(screen.getByRole('button', { name: 'Options for Office' })); fireEvent.click(screen.getByRole('button', { name: 'Remove', exact: true })); fireEvent.click(screen.getByRole('button', { name: 'Remove', exact: true })); expect(listPanels()).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Scan to add panel' })); fireEvent.click(screen.getByRole('button', { name: 'Enter code instead' })); expect(scan).toHaveBeenCalled(); expect(manual).toHaveBeenCalled();
  });
  it('retains the current session after storage writes fail', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    const p = addPanel(saved)[0]; expect(listPanels()).toEqual([p]); expect(renamePanel(p.id, 'Private session')[0].name).toBe('Private session'); removePanel(p.id);
  });
});

describe('PWA installation and dialog behavior', () => {
  it('detects iOS, iPad desktop mode, standalone and persistent dismissal', () => {
    expect(isStandalone()).toBe(false); expect(isDismissed()).toBe(false); dismissInstall(); expect(isDismissed()).toBe(true);
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone'); expect(isIos()).toBe(true);
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Desktop'); vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel'); Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, get: () => 5 }); expect(isIos()).toBe(true);
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: true })) }); expect(isStandalone()).toBe(true);
  });
  it('captures a one-use install prompt and hides the banner after installation', async () => {
    const prompt = vi.fn().mockResolvedValue(undefined), event = new Event('beforeinstallprompt', { cancelable: true });
    Object.assign(event, { prompt, userChoice: Promise.resolve({ outcome: 'accepted' }) });
    const hook = renderHook(useInstallKind); expect(hook.result.current).toBeNull();
    act(() => fireEvent(window, event)); expect(event.defaultPrevented).toBe(true); expect(hook.result.current).toBe('prompt');
    render(<InstallBanner />); expect(screen.getByRole('button', { name: 'Install Janus', exact: true })).toBeTruthy();
    await act(async () => { await promptInstall(); await promptInstall(); }); expect(prompt).toHaveBeenCalledTimes(1); expect(hook.result.current).toBeNull();
    expect(screen.queryByRole('region', { name: 'Install Janus' })).toBeNull();
  });
  it('shows iOS guidance and dismisses the regular banner while keeping prominent instructions', () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone'); const view = render(<InstallBanner />);
    expect(screen.getByText('Add to Home Screen')).toBeTruthy(); fireEvent.click(screen.getByRole('button', { name: 'Dismiss install tip' })); expect(screen.queryByText('Add to Home Screen')).toBeNull();
    view.rerender(<InstallBanner prominent />); expect(screen.getByText('Add to Home Screen')).toBeTruthy();
  });
  it('shows accessible dialog title and calls its close control', () => {
    const close = vi.fn(); render(<Dialog title="Panel options" onClose={close}><p>Options</p></Dialog>);
    expect(screen.getByRole('dialog', { name: 'Panel options' })).toBeTruthy(); fireEvent.keyDown(window, { key: 'Escape' }); expect(close).toHaveBeenCalled();
  });
});

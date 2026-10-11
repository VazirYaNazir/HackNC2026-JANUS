// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient, type Session } from '@janus/client';
import type { GeneratedDraft, GenerationRequest, JobView, LogicalCanvasLayout, ServerMessage } from '@janus/schema';
import Studio from '../src/App';
import { laptopProject } from '../src/editor/project';
import './dom';

const request: GenerationRequest = { description: 'Compute a checksum', operation: 'compute', platform: 'Darwin', architecture: 'arm64', capabilities: [], dependencies: [], output_protocol: 'json-v1' };
const draft: GeneratedDraft = { program_id: 'draft', program: { ...request, language: 'c11', source: '#include <stdio.h>\nint main(void) { puts("checksum"); return 0; }\n', summary: 'Compute a checksum' } };
const session: Session = { device_id: 'laptop', online: true, generation_available: true, target: { platform: 'Darwin', architecture: 'arm64', build_boundary_available: false, execution_boundary_available: false } };

function backend(current = session, project = laptopProject()) {
  let layout = project, revision = 1;
  let currentSession = current;
  let receivePresence: ((message: ServerMessage) => void) | undefined;
  const definitions = new Map<string, GenerationRequest>();
  vi.spyOn(PanelClient.prototype, 'request').mockImplementation(async path => {
    if (path === '/api/v1/studio/session' || path === '/api/v1/panels/session') return currentSession;
    if (path === '/api/layout') return { layout, revision };
    if (path === '/api/v1/agent-jobs') return { jobs: [] };
    if (path === '/api/activity') return { available: false, events: [] };
    if (path === '/api/v1/studio/messaging') return { enabled: false, connected: false };
    throw new Error(`Unexpected API: ${path}`);
  });
  vi.spyOn(PanelClient.prototype, 'subscribe').mockImplementation((receive, status) => { receivePresence = receive; status('connected'); receive({ type: 'auth_ok' }); return () => {}; });
  const save = vi.spyOn(PanelClient.prototype, 'save').mockImplementation(async next => { layout = next as LogicalCanvasLayout; revision++; return { layout, revision }; });
  const configure = vi.spyOn(PanelClient.prototype, 'configure').mockImplementation(async (action, next) => { definitions.set(action, next); return {}; });
  const generate = vi.spyOn(PanelClient.prototype, 'generate').mockResolvedValue(draft);
  const plan = vi.spyOn(PanelClient.prototype, 'plan').mockResolvedValue(request);
  const submitSource = vi.spyOn(PanelClient.prototype, 'submitSource').mockImplementation(async (_device, job_id) => ({ job_id, device_id: current.device_id, state: 'QUEUED', reason: null, result: null, elapsed_ms: 0, cancellation_confirmed: false } as JobView));
  const dispatch = vi.spyOn(PanelClient.prototype, 'dispatch');
  const prepare = vi.spyOn(PanelClient.prototype, 'prepare').mockResolvedValue({ request, request_sha256: 'digest', checked: true, matches: [] });
  return { save, configure, generate, plan, submitSource, dispatch, prepare, definitions, presence: (next: Session) => { currentSession = next; receivePresence?.({ type: 'agent_presence', session: next }); }, layout: () => layout };
}

async function connect() {
  fireEvent.click(screen.getByRole('button', { name: 'Connect backend' }));
  fireEvent.change(screen.getByLabelText('Laptop controller credential'), { target: { value: 'controller' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true })); });
}

function submitPrompt() {
  fireEvent.change(screen.getByLabelText('AI assistant prompt'), { target: { value: '  Compute a checksum  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate', exact: true }));
}

describe('Studio C source generation', () => {
  it('accepts a prompt with no selected widget or isolation adapter and reviews source without requesting execution', async () => {
    const b = backend(); render(<Studio />); await connect();
    expect((screen.getByLabelText('AI assistant prompt') as HTMLInputElement).disabled).toBe(false);
    expect(screen.queryByRole('button', { name: /Resize/ })).toBeNull();
    submitPrompt();
    await screen.findByRole('dialog', { name: 'Generation complete' });
    expect(b.generate).toHaveBeenCalledWith(request, expect.any(AbortSignal));
    expect(screen.getByLabelText('C source').textContent).toBe(draft.program.source);
    expect(screen.getByText('This source has not been validated or executed.')).toBeTruthy();
    expect(within(screen.getByRole('dialog', { name: 'Generation complete' })).getByText(/Execution is unavailable on this laptop/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Download C source' })).toBeTruthy();
    expect(b.save).not.toHaveBeenCalled(); expect(b.configure).not.toHaveBeenCalled(); expect(b.dispatch).not.toHaveBeenCalled(); expect(b.prepare).not.toHaveBeenCalled();
  });

  it('generates while the agent is offline and offers a target choice without changing any widget binding', async () => {
    const b = backend({ ...session, online: false, target: null }); render(<Studio />); await connect();
    expect(screen.getByLabelText('Platform')).toBeTruthy(); submitPrompt();
    await screen.findByRole('dialog', { name: 'Generation complete' });
    expect(screen.getByText(/Connect your laptop agent to request execution/)).toBeTruthy();
    expect(b.generate).toHaveBeenCalledTimes(1); expect(b.save).not.toHaveBeenCalled(); expect(b.dispatch).not.toHaveBeenCalled();
  });

  it('explains a missing Gemini key and prevents even programmatic submission', async () => {
    const b = backend({ ...session, generation_available: false }); render(<Studio />); await connect();
    const input = screen.getByLabelText('AI assistant prompt') as HTMLInputElement;
    expect(input.disabled).toBe(true); expect(screen.getByText(/Generation needs a Gemini API key/)).toBeTruthy();
    fireEvent.change(input, { target: { value: request.description } });
    fireEvent.submit(screen.getByRole('form', { name: 'AI assistant toolbar' }));
    expect(b.generate).not.toHaveBeenCalled(); expect(b.configure).not.toHaveBeenCalled();
  });

  it('retains the prompt after provider failure and allows retry', async () => {
    const b = backend(); b.generate.mockRejectedValueOnce(new Error('Gemini is temporarily unavailable'));
    render(<Studio />); await connect(); submitPrompt();
    await screen.findByText('Gemini is temporarily unavailable');
    expect((screen.getByLabelText('AI assistant prompt') as HTMLInputElement).value).toBe('  Compute a checksum  ');
    expect(screen.queryByRole('dialog', { name: 'Generation complete' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Generate', exact: true }));
    await screen.findByRole('dialog', { name: 'Generation complete' }); expect(b.generate).toHaveBeenCalledTimes(2);
  });

  it('clears source review and cancels its owned signal when the user connects to another backend', async () => {
    const b = backend(); render(<Studio />); await connect(); submitPrompt();
    await screen.findByRole('dialog', { name: 'Generation complete' });
    const oldSignal = b.generate.mock.calls[0][1]!;
    expect(oldSignal.aborted).toBe(false);
    // The current dialog does not trap background keyboard focus. Tab can leave
    // its last button and reach the existing backend connection button.
    const keyboard = userEvent.setup();
    screen.getByRole('button', { name: 'Close', exact: true }).focus();
    const connectionButton = screen.getByRole('button', { name: 'Laptop online · connected' });
    for (let steps = 0; document.activeElement !== connectionButton && steps < 6; steps++) await keyboard.tab();
    expect(document.activeElement).toBe(connectionButton);
    await keyboard.keyboard('{Enter}');
    fireEvent.change(screen.getByLabelText('Backend address'), { target: { value: 'http://localhost:8001' } });
    fireEvent.change(screen.getByLabelText('Laptop controller credential'), { target: { value: 'another-controller' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true })); });
    expect(oldSignal.aborted).toBe(true);
    expect(screen.queryByRole('dialog', { name: 'Generation complete' })).toBeNull();
    expect(screen.queryByLabelText('C source')).toBeNull();
    submitPrompt();
    await screen.findByRole('dialog', { name: 'Generation complete' });
    expect(b.generate.mock.contexts[1]).toMatchObject({ host: 'http://localhost:8001', token: 'another-controller' });
    expect(b.generate.mock.calls[1][1]!.aborted).toBe(false);
  });

  it('ignores delayed launcher bootstrap after a manual connection while that backend is generating', async () => {
    const b = backend(); let finishGeneration!: (value: GeneratedDraft) => void;
    b.generate.mockImplementation(() => new Promise(resolve => { finishGeneration = resolve; }));
    window.history.replaceState(null, '', '/#janus-start=http%3A%2F%2F127.0.0.1%3A12345&nonce=manual_choice_nonce_12345');
    const response = { ok: true, json: async () => ({ host: 'http://localhost:8000', token: 'automatic-controller' }) };
    let finishBootstrap!: (value: typeof response) => void;
    const fetch = vi.fn().mockReturnValue(new Promise<typeof response>(resolve => { finishBootstrap = resolve; }));
    vi.stubGlobal('fetch', fetch);
    render(<Studio />);
    expect(fetch).toHaveBeenCalledTimes(1); expect(window.location.hash).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Connect backend' }));
    fireEvent.change(screen.getByLabelText('Backend address'), { target: { value: 'http://localhost:8001' } });
    fireEvent.change(screen.getByLabelText('Laptop controller credential'), { target: { value: 'manual-controller' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Connect', exact: true })); });
    submitPrompt();
    const manualSignal = b.generate.mock.calls[0][1]!;
    await act(async () => { finishBootstrap(response); });
    expect(PanelClient.prototype.subscribe).toHaveBeenCalledTimes(1);
    expect(b.generate.mock.contexts[0]).toMatchObject({ host: 'http://localhost:8001', token: 'manual-controller' });
    expect(manualSignal.aborted).toBe(false);
    expect((screen.getByLabelText('AI assistant prompt') as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByLabelText('C source')).toBeNull();
    await act(async () => { finishGeneration(draft); });
    expect(screen.getByRole('dialog', { name: 'Generation complete' })).toBeTruthy();
    expect(manualSignal.aborted).toBe(false);
  });

  it('keeps the selected runnable control review flow and configures before publishing its binding', async () => {
    const b = backend({ ...session, target: { ...session.target!, build_boundary_available: true, execution_boundary_available: true } });
    render(<Studio />); await connect();
    fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true }));
    submitPrompt();
    await screen.findByRole('dialog', { name: 'Run Left click' });
    expect(b.configure).toHaveBeenCalledWith(expect.stringMatching(/^ai_[a-f0-9-]{36}$/), request);
    expect(b.configure.mock.invocationCallOrder[0]).toBeLessThan(b.save.mock.invocationCallOrder[0]);
    expect(b.generate).not.toHaveBeenCalled(); expect(b.dispatch).not.toHaveBeenCalled();
    expect(b.layout().widgets[2]).toMatchObject({ action: b.configure.mock.calls[0][0] });
  });

  it('preserves a working native binding if action configuration fails', async () => {
    const b = backend({ ...session, target: { ...session.target!, build_boundary_available: true, execution_boundary_available: true } });
    b.configure.mockRejectedValue(new Error('Configuration rejected'));
    render(<Studio />); await connect(); fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true })); submitPrompt();
    await screen.findByText('Configuration rejected');
    expect(b.save).not.toHaveBeenCalled(); expect(b.prepare).not.toHaveBeenCalled();
    expect(b.layout().widgets[2]).toMatchObject({ action: 'native_left_click' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('preserves an existing generated definition and published binding when saving fails', async () => {
    const project = laptopProject(); project.widgets[2] = { ...project.widgets[2], action: 'existing_generated' } as typeof project.widgets[number];
    const b = backend({ ...session, target: { ...session.target!, build_boundary_available: true, execution_boundary_available: true } }, project);
    const previous = { ...request, description: 'Previously approved request' };
    b.definitions.set('existing_generated', previous); b.save.mockRejectedValue(new Error('LAYOUT_REVISION_CONFLICT'));
    render(<Studio />); await connect(); fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true })); submitPrompt();
    await screen.findByText('LAYOUT_REVISION_CONFLICT');
    const [newAction] = b.configure.mock.calls[0];
    expect(newAction).toMatch(/^ai_[a-f0-9-]{36}$/); expect(newAction).not.toBe('existing_generated');
    expect(b.definitions.get('existing_generated')).toEqual(previous);
    expect(b.definitions.get(newAction)).toEqual(request);
    expect(b.layout().widgets[2]).toMatchObject({ action: 'existing_generated' });
    expect(b.prepare).not.toHaveBeenCalled(); expect(b.dispatch).not.toHaveBeenCalled(); expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('freezes runnable control edits until its new definition and layout are published', async () => {
    const b = backend({ ...session, target: { ...session.target!, build_boundary_available: true, execution_boundary_available: true } });
    let complete!: () => void; b.configure.mockImplementation(() => new Promise(resolve => { complete = () => resolve({}); }));
    const view = render(<Studio />); await connect(); fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true })); submitPrompt();
    expect(view.container.querySelector('.studio-body')!.hasAttribute('inert')).toBe(true);
    fireEvent.keyDown(document.body, { key: 'Delete' });
    await act(async () => { complete(); });
    expect(view.container.querySelector('.studio-body')!.hasAttribute('inert')).toBe(false);
    expect(b.layout().widgets.some(widget => widget.id === 'left-click')).toBe(true);
    expect(screen.getByRole('dialog', { name: 'Run Left click' })).toBeTruthy();
  });

  it('cancels pending draft generation when Studio is removed', async () => {
    const b = backend(); b.generate.mockImplementation(() => new Promise(() => {}));
    const view = render(<Studio />); await connect(); submitPrompt();
    await waitFor(() => expect(b.generate).toHaveBeenCalledTimes(1));
    const signal = b.generate.mock.calls[0][1]!;
    expect(signal.aborted).toBe(false); view.unmount(); expect(signal.aborted).toBe(true);
  });

  it('keeps generation blocked and preserves the prompt when selection changes during a pending request', async () => {
    const b = backend(); let complete!: (value: GeneratedDraft) => void;
    b.generate.mockImplementation(() => new Promise(resolve => { complete = resolve; }));
    render(<Studio />); await connect(); submitPrompt();
    expect((screen.getByRole('button', { name: 'Laptop online · connected' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Project', exact: true }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Laptop online · connected' }));
    expect(screen.queryByRole('dialog', { name: 'Connect to your backend' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true }));
    const input = screen.getByLabelText('AI assistant prompt') as HTMLInputElement;
    expect(input.disabled).toBe(true); expect(input.value).toBe('  Compute a checksum  '); expect(screen.getByText('Wait for the current request to finish.')).toBeTruthy();
    fireEvent.change(input, { target: { value: 'Another prompt' } }); fireEvent.submit(screen.getByRole('form', { name: 'AI assistant toolbar' }));
    expect(b.generate).toHaveBeenCalledTimes(1);
    await act(async () => { complete(draft); });
    expect(screen.getByRole('dialog', { name: 'Generation complete' })).toBeTruthy();
    expect(b.configure).not.toHaveBeenCalled(); expect(b.save).not.toHaveBeenCalled();
  });
});

const portableSession: Session = { ...session, target: { ...session.target!, build_boundary_available: true, execution_boundary_available: true, execution_target: { platform: 'Linux', architecture: 'arm64', profile: 'portable-c', capabilities: ['files.read', 'files.write', 'process.spawn', 'audio.volume'], dependencies: [] } } };
const portableRequest: GenerationRequest = { ...request, platform: 'Linux', operation: 'audio.volume', capabilities: ['audio.volume'] };
const portableDraft: GeneratedDraft = { ...draft, program: { ...draft.program, ...portableRequest } };

describe('Studio portable worker prompting', () => {
  it('plans minimum access and reviews exact Linux source before explicitly submitting a fresh standalone job', async () => {
    const b = backend(portableSession); b.plan.mockResolvedValue(portableRequest); b.generate.mockResolvedValue(portableDraft);
    render(<Studio />); await connect();
    expect((screen.getByLabelText('Choose minimum access automatically') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('network') as HTMLInputElement).disabled).toBe(true);
    submitPrompt();
    const review = await screen.findByRole('dialog', { name: 'Generation complete' });
    expect(b.plan).toHaveBeenCalledWith(request.description, expect.objectContaining({ platform: 'Linux', architecture: 'arm64' }), portableSession.target!.execution_target!.capabilities, expect.any(AbortSignal));
    expect(b.generate).toHaveBeenCalledWith(portableRequest, expect.any(AbortSignal));
    expect(within(review).getByText('Requested access: audio.volume')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Generate', exact: true }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByRole('form', { name: 'AI assistant toolbar' })); expect(b.plan).toHaveBeenCalledTimes(1);
    expect(within(review).getByText('Target: Linux / arm64')).toBeTruthy();
    expect(b.submitSource).not.toHaveBeenCalled(); expect(b.save).not.toHaveBeenCalled(); expect(b.configure).not.toHaveBeenCalled();
    fireEvent.click(within(review).getByRole('button', { name: 'Run on laptop' }));
    await screen.findByRole('dialog', { name: 'Recent requests' });
    expect(b.submitSource).toHaveBeenCalledWith('laptop', expect.stringMatching(/^[a-f0-9-]{36}$/), portableRequest, portableDraft.program, expect.any(AbortSignal));
    expect(within(screen.getByRole('dialog', { name: 'Recent requests' })).getAllByText('QUEUED')).toHaveLength(2); expect(b.dispatch).not.toHaveBeenCalled(); expect(b.save).not.toHaveBeenCalled();
  });
  it('narrows automatic access and lets manual settings constrain exact scope without planning', async () => {
    const b = backend(portableSession); b.plan.mockResolvedValue({ ...portableRequest, capabilities: [] }); b.generate.mockResolvedValue({ ...portableDraft, program: { ...portableDraft.program, capabilities: [] } });
    render(<Studio />); await connect(); fireEvent.click(screen.getByLabelText('audio.volume')); submitPrompt();
    await screen.findByRole('dialog', { name: 'Generation complete' });
    expect(b.plan.mock.calls[0][2]).toEqual(['files.read', 'files.write', 'process.spawn']);
    fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
    fireEvent.click(screen.getByLabelText('Choose minimum access automatically'));
    fireEvent.change(screen.getByLabelText('Operation'), { target: { value: 'volume' } });
    fireEvent.click(screen.getByLabelText('audio.volume')); submitPrompt();
    await screen.findByRole('dialog', { name: 'Generation complete' });
    expect(b.plan).toHaveBeenCalledTimes(1);
    expect(b.generate.mock.calls[1][0]).toEqual({ ...portableRequest, operation: 'volume' });
  });
  it('configures a selected control with the planned worker request before its existing execution review', async () => {
    const b = backend(portableSession); b.plan.mockResolvedValue(portableRequest); b.prepare.mockResolvedValue({ request: portableRequest, request_sha256: 'digest', checked: true, matches: [] });
    render(<Studio />); await connect(); fireEvent.click(screen.getByRole('button', { name: 'Left click', exact: true })); submitPrompt();
    await screen.findByRole('dialog', { name: 'Run Left click' });
    expect(b.configure).toHaveBeenCalledWith(expect.stringMatching(/^ai_/), portableRequest);
    expect(b.plan.mock.invocationCallOrder[0]).toBeLessThan(b.configure.mock.invocationCallOrder[0]);
    expect(b.generate).not.toHaveBeenCalled(); expect(b.submitSource).not.toHaveBeenCalled();
  });
  it('retains a rejected draft and uses a fresh request ID on retry without generating or modifying the layout again', async () => {
    const b = backend(portableSession); b.plan.mockResolvedValue(portableRequest); b.generate.mockResolvedValue(portableDraft);
    b.submitSource.mockRejectedValueOnce(new Error('AUTHORIZATION_REQUIRED'));
    render(<Studio />); await connect(); submitPrompt(); const review = await screen.findByRole('dialog', { name: 'Generation complete' });
    fireEvent.click(within(review).getByRole('button', { name: 'Run on laptop' })); await screen.findByText('AUTHORIZATION_REQUIRED');
    expect(within(review).getByLabelText('C source').textContent).toBe(portableDraft.program.source);
    fireEvent.click(within(review).getByRole('button', { name: 'Run on laptop' })); await screen.findByRole('dialog', { name: 'Recent requests' });
    expect(b.submitSource.mock.calls[0][1]).not.toBe(b.submitSource.mock.calls[1][1]); expect(b.generate).toHaveBeenCalledTimes(1); expect(b.save).not.toHaveBeenCalled();
  });
  it('retains prompt after planning failures and rejects a plan outside the selected access ceiling', async () => {
    const b = backend(portableSession); b.plan.mockRejectedValueOnce(new Error('CAPABILITY_UNAVAILABLE')).mockResolvedValueOnce({ ...portableRequest, capabilities: ['admin'] });
    render(<Studio />); await connect(); submitPrompt(); await screen.findByText('CAPABILITY_UNAVAILABLE');
    expect((screen.getByLabelText('AI assistant prompt') as HTMLInputElement).value).toBe('  Compute a checksum  ');
    fireEvent.click(screen.getByRole('button', { name: 'Generate', exact: true })); await screen.findByText('The access plan exceeds the selected target or allowed capabilities.');
    expect(b.generate).not.toHaveBeenCalled(); expect(b.configure).not.toHaveBeenCalled(); expect(b.submitSource).not.toHaveBeenCalled();
  });
  it('disables a reviewed draft when the current laptop disconnects or its supported access changes', async () => {
    const b = backend(portableSession); b.plan.mockResolvedValue(portableRequest); b.generate.mockResolvedValue(portableDraft);
    render(<Studio />); await connect(); submitPrompt(); const review = await screen.findByRole('dialog', { name: 'Generation complete' });
    act(() => b.presence({ ...portableSession, online: false, target: null }));
    expect((within(review).getByRole('button', { name: 'Run on laptop' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(review).getByText('Connect your laptop agent to request execution.')).toBeTruthy();
    act(() => b.presence({ ...portableSession, target: { ...portableSession.target!, execution_target: { ...portableSession.target!.execution_target!, capabilities: [] } } }));
    expect((within(review).getByRole('button', { name: 'Run on laptop' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(review).getByText(/does not support the requested access: audio.volume/)).toBeTruthy();
    fireEvent.click(within(review).getByRole('button', { name: 'Run on laptop' })); expect(b.submitSource).not.toHaveBeenCalled();
    act(() => b.presence({ ...portableSession, target: { ...portableSession.target!, execution_target: { ...portableSession.target!.execution_target!, architecture: 'x86_64' } } }));
    expect(within(review).getByText(/different system/)).toBeTruthy();
    act(() => b.presence(portableSession)); expect((within(review).getByRole('button', { name: 'Run on laptop' }) as HTMLButtonElement).disabled).toBe(false);
  });
  it('cancels owned automatic planning before it can generate when Studio is removed', async () => {
    const b = backend(portableSession); let finish!: (value: GenerationRequest) => void;
    b.plan.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const view = render(<Studio />); await connect(); submitPrompt();
    const signal = b.plan.mock.calls[0][3]!; expect(signal.aborted).toBe(false);
    view.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => finish(portableRequest)); expect(b.generate).not.toHaveBeenCalled(); expect(b.configure).not.toHaveBeenCalled();
  });

});

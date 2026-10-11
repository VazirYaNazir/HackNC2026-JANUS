// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient, RequestDialog, type Prepared, type Session } from '@janus/client';
import type { GeneratedDraft, JobView } from '@janus/schema';
import { laptopProject } from '../src/editor/project';
import './dom';

const prepared: Prepared = {
  request: { description: 'Read the selected folder size', operation: 'folder_size', platform: 'Darwin', architecture: 'arm64', capabilities: ['files.read'], dependencies: [], output_protocol: 'json-v1' },
  request_sha256: 'request-digest', checked: true,
  matches: [{ id: 'saved-program', description: 'Read folder size', summary: 'Folder size', match: 'exact' }],
};
const draft: GeneratedDraft = {
  program_id: 'draft-program',
  program: { language: 'c11', source: '#include <stdio.h>\nint main(void) { puts("folder size"); return 0; }\n', summary: 'Read the folder size', ...prepared.request },
};
const session: Session = {
  device_id: 'laptop', online: true, generation_available: true,
  target: { platform: 'Darwin', architecture: 'arm64', build_boundary_available: false, execution_boundary_available: false },
};
const client = () => new PanelClient({ host: 'http://localhost:8000', token: 'controller-fixture' });
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const path = (url: URL | RequestInfo) => new URL(String(url)).pathname;

describe('source review without a local isolation adapter', () => {
  it('generates the prepared scoped request without dispatching a job, closing, or claiming execution', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async (url) => path(url) === '/api/v1/panel-actions/prepare' ? response(prepared) : response(draft));
    vi.stubGlobal('fetch', fetch);
    const close = vi.fn(), submitted = vi.fn();
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} value="folder" session={session} onClose={close} onSubmitted={submitted} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    expect(screen.queryByLabelText('Request ID')).toBeNull();
    expect(screen.queryByRole('group', { name: /Similar programs/ })).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await waitFor(() => expect(screen.getByLabelText('C source').textContent).toBe(draft.program.source));
    expect(screen.getByText(draft.program.summary)).toBeTruthy();
    expect(screen.getByText('This source has not been validated or executed.')).toBeTruthy();
    expect(fetch.mock.calls.map(([url]) => path(url))).toEqual(['/api/v1/panel-actions/prepare', '/api/v1/programs/generate']);
    const [, generationOptions] = fetch.mock.calls[1];
    expect(JSON.parse(String(generationOptions?.body))).toEqual(prepared.request);
    expect(new Headers(generationOptions?.headers).get('Authorization')).toBe('Bearer controller-fixture');
    expect(close).not.toHaveBeenCalled();
    expect(submitted).not.toHaveBeenCalled();
  });

  it.each([
    { build_boundary_available: false, execution_boundary_available: true },
    { build_boundary_available: true, execution_boundary_available: false },
  ])('requires source review when either validation boundary is missing: %j', async (boundaries) => {
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => response(prepared)));
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={{ ...session, target: { ...session.target!, ...boundaries } }} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    expect(screen.queryByRole('button', { name: /request execution/ })).toBeNull();
  });

  it.each([null, { ...session, online: false }, { ...session, target: null }])('offers source review when an explicitly supplied session cannot execute: %j', async (unavailableSession) => {
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => response(prepared)));
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={unavailableSession} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    expect(screen.queryByRole('button', { name: /request execution/ })).toBeNull();
  });

  it('reports generation failures and allows retry while preserving an existing draft', async () => {
    let generations = 0;
    const fetch = vi.fn<typeof globalThis.fetch>(async (url) => {
      if (path(url) === '/api/v1/panel-actions/prepare') return response(prepared);
      generations++;
      return generations === 2 ? response({ detail: 'Gemini is temporarily unavailable' }, 503) : response(draft);
    });
    vi.stubGlobal('fetch', fetch);
    const close = vi.fn(), submitted = vi.fn();
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={session} onClose={close} onSubmitted={submitted} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await screen.findByLabelText('C source');
    fireEvent.click(screen.getByRole('button', { name: 'Generate source again' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Gemini is temporarily unavailable'));
    expect(screen.getByLabelText('C source').textContent).toBe(draft.program.source);
    fireEvent.click(screen.getByRole('button', { name: 'Generate source again' }));
    await waitFor(() => expect(generations).toBe(3));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(close).not.toHaveBeenCalled();
    expect(submitted).not.toHaveBeenCalled();
    expect(fetch.mock.calls.some(([url]) => path(url).endsWith('/dispatch'))).toBe(false);
  });

  it('explains a missing Gemini credential and disables generation without calling the generation endpoint', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => response(prepared));
    vi.stubGlobal('fetch', fetch);
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={{ ...session, generation_available: false }} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('alert').textContent).toContain('Gemini API key');
    expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(true);
    expect(fetch.mock.calls.some(([url]) => path(url).endsWith('/generate'))).toBe(false);
  });

  it('downloads only the displayed C source and releases its temporary URL', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async (url) => path(url) === '/api/v1/panel-actions/prepare' ? response(prepared) : response(draft)));
    const createObjectURL = vi.fn<(blob: Blob) => string>(() => 'blob:generated-source'), revokeObjectURL = vi.fn();
    const NativeURL = URL;
    vi.stubGlobal('URL', class extends NativeURL { static createObjectURL = createObjectURL; static revokeObjectURL = revokeObjectURL; });
    let downloadName = '', downloadURL = '';
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloadName = this.download; downloadURL = this.href; });
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await screen.findByLabelText('C source');
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: 'Download C source' }));
    expect(downloadName).toBe('janus-generated.c');
    expect(downloadURL).toBe('blob:generated-source');
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect((createObjectURL.mock.calls[0][0] as Blob).type).toBe('text/x-c;charset=utf-8');
    vi.advanceTimersByTime(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:generated-source');
  });

  it('renders generated source and summary as text even when they contain HTML', async () => {
    const source = '<script>window.janusInjected = true</script><img src="bad" onerror="window.janusInjected = true">';
    const untrustedDraft = { ...draft, program: { ...draft.program, source, summary: '<img src="bad">' } };
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async (url) => path(url) === '/api/v1/panel-actions/prepare' ? response(prepared) : response(untrustedDraft)));
    const view = render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await waitFor(() => expect(screen.getByLabelText('C source').textContent).toBe(source));
    expect(screen.getByRole('heading', { level: 3 }).textContent).toBe(untrustedDraft.program.summary);
    expect(view.container.querySelector('script, img')).toBeNull();
    expect('janusInjected' in window).toBe(false);
  });

  it('cancels a pending source generation when the dialog is removed', async () => {
    let generationSignal: AbortSignal | null | undefined;
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async (url, options) => {
      if (path(url) === '/api/v1/panel-actions/prepare') return response(prepared);
      generationSignal = options?.signal;
      return new Promise<Response>(() => {});
    }));
    const view = render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await waitFor(() => expect(generationSignal).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Generating…' }).hasAttribute('disabled')).toBe(true);
    view.unmount();
    expect(generationSignal?.aborted).toBe(true);
  });
});

describe('request context changes', () => {
  it('aborts a previous laptop preparation and ignores its late response', async () => {
    const oldClient = client(), newClient = new PanelClient({ host: 'http://localhost:8001', token: 'new-controller' });
    const widget = laptopProject().widgets[2];
    let oldSignal: AbortSignal | null | undefined;
    let resolveOld!: (response: Response) => void, resolveNew!: (response: Response) => void;
    const newPrepared = { ...prepared, request: { ...prepared.request, description: 'Request for the new laptop' } };
    const fetch = vi.fn<typeof globalThis.fetch>((url, options) => {
      if (path(url).endsWith('/generate')) return Promise.resolve(response(draft));
      if (new URL(String(url)).port === '8000') {
        oldSignal = options?.signal;
        return new Promise<Response>((resolve) => { resolveOld = resolve; });
      }
      return new Promise<Response>((resolve) => { resolveNew = resolve; });
    });
    vi.stubGlobal('fetch', fetch);
    const view = render(<RequestDialog client={oldClient} widget={widget} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(resolveOld).toBeTruthy());
    view.rerender(<RequestDialog client={newClient} widget={widget} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => resolveOld(response(prepared)));
    expect(screen.queryByText(prepared.request.description)).toBeNull();
    expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(true);
    await act(async () => resolveNew(response(newPrepared)));
    expect(screen.getByText(newPrepared.request.description)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await screen.findByLabelText('C source');
    expect(new URL(String(fetch.mock.calls[2][0])).port).toBe('8001');
    expect(JSON.parse(String(fetch.mock.calls[2][1]?.body))).toEqual(newPrepared.request);
  });

  it('cancels source generation on a laptop change and ignores its late result while the new generation remains busy', async () => {
    const oldClient = client(), newClient = new PanelClient({ host: 'http://localhost:8001', token: 'new-controller' });
    const widget = laptopProject().widgets[2];
    let oldSignal: AbortSignal | null | undefined;
    let resolveOld!: (response: Response) => void, resolveNew!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>((url, options) => {
      if (path(url).endsWith('/prepare')) return Promise.resolve(response(prepared));
      if (new URL(String(url)).port === '8000') {
        oldSignal = options?.signal;
        return new Promise<Response>((resolve) => { resolveOld = resolve; });
      }
      return new Promise<Response>((resolve) => { resolveNew = resolve; });
    }));
    const view = render(<RequestDialog client={oldClient} widget={widget} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await waitFor(() => expect(resolveOld).toBeTruthy());
    view.rerender(<RequestDialog client={newClient} widget={widget} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    expect(oldSignal?.aborted).toBe(true);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await act(async () => resolveOld(response(draft)));
    expect(screen.queryByLabelText('C source')).toBeNull();
    expect(screen.getByRole('button', { name: 'Generating…' }).hasAttribute('disabled')).toBe(true);
    const newDraft = { ...draft, program: { ...draft.program, source: 'int main(void) { return 1; }' } };
    await act(async () => resolveNew(response(newDraft)));
    expect(screen.getByLabelText('C source').textContent).toBe(newDraft.program.source);
  });

  it.each(['client', 'widget', 'value'] as const)('clears a completed draft and prepared request when %s changes', async (changed) => {
    const originalClient = client(), originalWidget = laptopProject().widgets[2];
    let preparations = 0;
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async (url) => {
      if (path(url).endsWith('/generate')) return response(draft);
      preparations++;
      return preparations === 1 ? response(prepared) : new Promise<Response>(() => {});
    }));
    const view = render(<RequestDialog client={originalClient} widget={originalWidget} value="old value" session={session} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Generate source' }));
    await screen.findByLabelText('C source');
    view.rerender(<RequestDialog client={changed === 'client' ? client() : originalClient} widget={changed === 'widget' ? { ...originalWidget, id: 'another-widget' } : originalWidget} value={changed === 'value' ? 'new value' : 'old value'} session={session} onClose={() => {}} onSubmitted={() => {}} />);
    expect(screen.queryByLabelText('C source')).toBeNull();
    expect(screen.queryByText(prepared.request.description)).toBeNull();
    expect(screen.getByRole('button', { name: 'Generate source' }).hasAttribute('disabled')).toBe(true);
    expect(preparations).toBe(2);
  });

  it('resets the reuse selection and Request ID for a changed control', async () => {
    const originalClient = client(), widget = laptopProject().widgets[2];
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => response(prepared)));
    const view = render(<RequestDialog client={originalClient} widget={widget} onClose={() => {}} onSubmitted={() => {}} />);
    await screen.findByRole('group', { name: /Similar programs/ });
    fireEvent.click(screen.getAllByRole('radio')[1]);
    fireEvent.change(screen.getByLabelText('Request ID'), { target: { value: 'old-request-id' } });
    expect(screen.getByRole('button', { name: 'Reuse and request execution' })).toBeTruthy();
    view.rerender(<RequestDialog client={originalClient} widget={{ ...widget, id: 'another-widget' }} onClose={() => {}} onSubmitted={() => {}} />);
    await screen.findByRole('group', { name: /Similar programs/ });
    expect((screen.getByLabelText('Request ID') as HTMLInputElement).value).not.toBe('old-request-id');
    expect((screen.getAllByRole('radio')[0] as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole('button', { name: 'Generate and request execution' })).toBeTruthy();
  });
});

describe('execution with an available isolation adapter', () => {
  it('keeps explicit reuse approval and dispatches a fresh locally authorized job', async () => {
    const job: JobView = { job_id: 'new-approved-job', device_id: 'laptop', state: 'QUEUED', reason: null, result: null, elapsed_ms: 0, cancellation_confirmed: false };
    const fetch = vi.fn<typeof globalThis.fetch>(async (url) => path(url) === '/api/v1/panel-actions/prepare' ? response(prepared) : response(job));
    vi.stubGlobal('fetch', fetch);
    const close = vi.fn(), submitted = vi.fn();
    render(<RequestDialog client={client()} widget={laptopProject().widgets[2]} session={{ ...session, target: { ...session.target!, build_boundary_available: true, execution_boundary_available: true } }} onClose={close} onSubmitted={submitted} />);
    await screen.findByRole('group', { name: /Similar programs/ });
    expect(fetch).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getAllByRole('radio')[1]);
    fireEvent.change(screen.getByLabelText('Request ID'), { target: { value: job.job_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Reuse and request execution' }));
    await waitFor(() => expect(submitted).toHaveBeenCalledWith(job));
    expect(close).toHaveBeenCalledTimes(1);
    expect(path(fetch.mock.calls[1][0])).toBe('/api/v1/panel-actions/dispatch');
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual(expect.objectContaining({ job_id: job.job_id, program_id: 'saved-program', request_sha256: prepared.request_sha256 }));
    expect(fetch.mock.calls.some(([url]) => path(url).endsWith('/generate'))).toBe(false);
  });
});

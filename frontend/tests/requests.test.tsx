// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelClient, RequestDialog } from '@janus/client';
import type { GenerationRequest, JobView } from '@janus/schema';
import { laptopProject } from '../src/editor/project';
import './dom';

const request: GenerationRequest = { description: 'Compute value', operation: 'compute', platform: 'Darwin', architecture: 'arm64', capabilities: [], dependencies: [], output_protocol: 'json-v1' };
const job: JobView = { job_id: 'fresh', device_id: 'laptop', state: 'QUEUED', reason: null, result: null, elapsed_ms: 0, cancellation_confirmed: false };

describe('explicit generated program approval and reuse', () => {
  it('prepares without dispatch, requires an explicit reuse choice and sends the fresh ID and request digest', async () => {
    const client = new PanelClient({ host: 'http://localhost:8000', token: 'fixture' }), submit = vi.fn(), close = vi.fn();
    vi.spyOn(client, 'prepare').mockResolvedValue({ request, request_sha256: 'digest', checked: true, matches: [{ id: 'program', description: 'Previous compute', summary: 'Compute', match: 'exact' }] });
    const dispatch = vi.spyOn(client, 'dispatch').mockResolvedValue(job);
    render(<RequestDialog client={client} widget={laptopProject().widgets[2]} onClose={close} onSubmitted={submit} />);
    await waitFor(() => expect(screen.getByRole('group', { name: /Similar programs/ })).toBeTruthy()); expect(dispatch).not.toHaveBeenCalled();
    expect(screen.getAllByRole('radio')[0].getAttribute('checked')).not.toBeNull();
    fireEvent.click(screen.getAllByRole('radio')[1]); fireEvent.change(screen.getByLabelText('Request ID'), { target: { value: 'fresh' } }); fireEvent.click(screen.getByRole('button', { name: 'Reuse and request execution' }));
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ job_id: 'fresh', program_id: 'program', request_sha256: 'digest' })));
    expect(submit).toHaveBeenCalledWith(job); expect(close).toHaveBeenCalled();
  });
  it('allows new generation when similarity is unavailable and retains failed requests for retry', async () => {
    const client = new PanelClient({ host: 'http://localhost:8000', token: 'fixture' });
    vi.spyOn(client, 'prepare').mockResolvedValue({ request, request_sha256: 'digest', checked: false, matches: [] });
    const dispatch = vi.spyOn(client, 'dispatch').mockRejectedValueOnce(new Error('Local grant required')).mockResolvedValue(job), close = vi.fn();
    render(<RequestDialog client={client} widget={laptopProject().widgets[2]} onClose={close} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Similarity checking is unavailable/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Request ID'), { target: { value: 'fresh' } }); fireEvent.click(screen.getByRole('button', { name: 'Generate and request execution' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Local grant required')); expect(close).not.toHaveBeenCalled();
    expect(dispatch.mock.calls[0][0].program_id).toBeUndefined(); fireEvent.click(screen.getByRole('button', { name: 'Generate and request execution' })); await waitFor(() => expect(close).toHaveBeenCalled());
  });
  it('blocks dispatch on preparation failure and aborts an owned preparation request on close', async () => {
    const client = new PanelClient({ host: 'http://localhost:8000', token: 'fixture' }); const prepare = vi.spyOn(client, 'prepare').mockRejectedValue(new Error('Not configured'));
    const view = render(<RequestDialog client={client} widget={laptopProject().widgets[2]} onClose={() => {}} onSubmitted={() => {}} />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Not configured'));
    expect(screen.getByRole('button', { name: 'Generate and request execution' }).hasAttribute('disabled')).toBe(true); view.unmount(); expect(prepare.mock.calls[0][1]?.aborted).toBe(true);
  });
});

import { useEffect, useState, type FormEvent } from 'react';
import type { JobView, Widget, WidgetValue } from '@janus/schema';
import { PanelClient, type Prepared } from './api';

/** Default location documented in agent/docs/validation-protocol.md; the agent reads it only when it starts. */
const GRANT_FILE = 'agent/.validation-policy.json';

export function RequestDialog({ client, widget, value, onClose, onSubmitted }: {
  client: PanelClient; widget: Widget; value?: WidgetValue;
  onClose: () => void; onSubmitted: (job: JobView) => void;
}) {
  const [initialId] = useState(() => crypto.randomUUID());
  const [jobId, setJobId] = useState<string>(initialId);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    void client.prepare({ widget_id: widget.id, job_id: initialId, value }, abort.signal)
      .then(setPrepared).catch((reason) => { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : 'Request preparation failed.'); });
    return () => abort.abort();
  }, [client, initialId, value, widget.id]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      const job = await client.dispatch({ widget_id: widget.id, job_id: jobId, value, request_sha256: prepared?.request_sha256, ...(choice ? { program_id: choice } : {}) });
      onSubmitted(job); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not submit the request.'); }
    finally { setBusy(false); }
  };

  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="request-heading">
    <h2 id="request-heading">Run {widget.label}</h2>
    <p>The laptop must approve this request before it runs.</p>
    <form onSubmit={(event) => void submit(event)}>
      <label>Request ID<input value={jobId} maxLength={128} pattern="[A-Za-z0-9_-]{1,128}" required onChange={(event) => setJobId(event.target.value)} /></label>
      <p className="janus-muted">Each execution needs a fresh ID. Approval happens on the laptop: JANUS startup asks whether to enable the local policy for this session. If session approval is disabled, add a grant for this exact Request ID to <code>{GRANT_FILE}</code>, pass it with <code>--validation-policy</code>, and restart the agent. Without local approval the request stops at “authorization required”. Generated programs also require an installed isolation adapter.</p>
      {prepared && <><p>{prepared.request.description}</p><p className="janus-muted">Requested access: {prepared.request.capabilities.join(', ') || 'none'}</p></>}
      {!prepared && !error && <p role="status">Checking for reusable programs…</p>}
      {prepared?.matches.length ? <fieldset><legend>Similar programs found — choose whether to reuse</legend>
        <label><input type="radio" name="program" checked={!choice} onChange={() => setChoice('')} />Generate a new program</label>
        {prepared.matches.map((match) => <label key={match.id}><input type="radio" name="program" checked={choice === match.id} onChange={() => setChoice(match.id)} /><span>{match.summary}<small>{match.description} ({match.match} match)</small></span></label>)}
      </fieldset> : prepared && <p>No matching program found. A new program will be generated.</p>}
      {prepared && !prepared.checked && <p>Similarity checking is unavailable. You can generate a new program.</p>}
      {error && <p className="janus-error" role="alert">{error}</p>}
      <div className="janus-actions"><button type="button" onClick={onClose} disabled={busy}>Cancel</button><button type="submit" disabled={busy || !prepared || !jobId.trim()}>{busy ? 'Submitting…' : choice ? 'Reuse and request execution' : 'Generate and request execution'}</button></div>
    </form>
  </section></div>;
}

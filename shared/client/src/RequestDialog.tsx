import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { GeneratedDraft, JobView, Widget, WidgetValue } from '@janus/schema';
import { PanelClient, type Prepared, type Session } from './api';
import { executionUnavailable } from './execution';
import { SourceReview } from './SourceReview';

/** Default location documented in agent/docs/validation-protocol.md; the agent reads it only when it starts. */
const GRANT_FILE = 'agent/.validation-policy.json';

interface RequestDialogProps {
  client: PanelClient; widget: Widget; value?: WidgetValue; session?: Session | null;
  onClose: () => void; onSubmitted: (job: JobView) => void;
}

export function RequestDialog(props: RequestDialogProps) {
  // Changing laptops or controls starts a fresh request and cancels the previous dialog's work.
  const contextKey = useMemo(() => crypto.randomUUID(), [props.client, props.widget, props.value]);
  return <RequestDialogForm key={contextKey} {...props} />;
}

function RequestDialogForm({ client, widget, value, session, onClose, onSubmitted }: RequestDialogProps) {
  const [initialId] = useState(() => crypto.randomUUID());
  const [jobId, setJobId] = useState<string>(initialId);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<GeneratedDraft | null>(null);
  const generation = useRef<AbortController | null>(null);
  const preparation = useRef<AbortController | null>(null);
  const executionReason = session === undefined ? null : executionUnavailable(session, prepared?.request);
  const sourceOnly = executionReason !== null;
  const needsReconfiguration = !!prepared && session?.online === true && session.target?.build_boundary_available === true && session.target.execution_boundary_available === true && !!executionReason;
  const generationUnavailable = session?.generation_available === false;
  useEffect(() => {
    const abort = new AbortController();
    preparation.current = abort;
    void client.prepare({ widget_id: widget.id, job_id: initialId, value }, abort.signal)
      .then((result) => { if (!abort.signal.aborted) setPrepared(result); }).catch((reason) => { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : 'Request preparation failed.'); });
    return () => { abort.abort(); generation.current?.abort(); };
  }, [client, initialId, value, widget.id]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const context = preparation.current;
    if (busy || !prepared || !context || context.signal.aborted || (sourceOnly && generationUnavailable)) return;
    setBusy(true); setError('');
    try {
      if (sourceOnly) {
        const abort = new AbortController();
        generation.current = abort;
        const generated = await client.generate(prepared.request, abort.signal);
        if (!abort.signal.aborted) setDraft(generated);
        return;
      }
      const job = await client.dispatch({ widget_id: widget.id, job_id: jobId, value, request_sha256: prepared?.request_sha256, ...(choice ? { program_id: choice } : {}) });
      if (!context.signal.aborted) { onSubmitted(job); onClose(); }
    } catch (reason) { if (!context.signal.aborted && !generation.current?.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not submit the request.'); }
    finally { if (!context.signal.aborted) setBusy(false); }
  };

  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="request-heading">
    <h2 id="request-heading">{sourceOnly ? 'Generate source for' : 'Run'} {widget.label}</h2>
    <p>{sourceOnly ? `${executionReason} ${needsReconfiguration ? 'Reconfigure this control in Studio for the current worker. Source generation below keeps this control’s saved target and access.' : 'You can still generate and review C source.'}` : 'The laptop must approve this request before it runs.'}</p>
    <form onSubmit={(event) => void submit(event)}>
      {!sourceOnly && <><label>Request ID<input value={jobId} maxLength={128} pattern="[A-Za-z0-9_-]{1,128}" required onChange={(event) => setJobId(event.target.value)} /></label>
      <p className="janus-muted">Each execution needs a fresh ID. Approval happens on the laptop: JANUS startup asks whether to enable the local policy for this session. If session approval is disabled, add a grant for this exact Request ID to <code>{GRANT_FILE}</code>, pass it with <code>--validation-policy</code>, and restart the agent. Without local approval the request stops at “authorization required”. Generated programs run in the isolated Docker Desktop worker, which JANUS prepares at startup. Start Docker Desktop and restart JANUS if the worker is unavailable.</p></>}
      {prepared && <><p>{prepared.request.description}</p><p className="janus-muted">Requested access: {prepared.request.capabilities.join(', ') || 'none'}</p></>}
      {!prepared && !error && <p role="status">Checking for reusable programs…</p>}
      {!sourceOnly && (prepared?.matches.length ? <fieldset><legend>Similar programs found — choose whether to reuse</legend>
        <label><input type="radio" name="program" checked={!choice} onChange={() => setChoice('')} />Generate a new program</label>
        {prepared.matches.map((match) => <label key={match.id}><input type="radio" name="program" checked={choice === match.id} onChange={() => setChoice(match.id)} /><span>{match.summary}<small>{match.description} ({match.match} match)</small></span></label>)}
      </fieldset> : prepared && <p>No matching program found. A new program will be generated.</p>)}
      {prepared && !prepared.checked && <p>Similarity checking is unavailable. You can generate a new program.</p>}
      {sourceOnly && generationUnavailable && <p className="janus-error" role="alert">Generation needs a Gemini API key in the backend environment. Add the key and restart JANUS.</p>}
      {draft && <SourceReview draft={draft} />}
      {error && <p className="janus-error" role="alert">{error}</p>}
      <div className="janus-actions"><button type="button" onClick={onClose} disabled={busy}>{draft ? 'Close' : 'Cancel'}</button><button type="submit" disabled={busy || !prepared || (!sourceOnly && !jobId.trim()) || (sourceOnly && generationUnavailable)}>{busy ? sourceOnly ? 'Generating…' : 'Submitting…' : sourceOnly ? draft ? 'Generate source again' : 'Generate source' : choice ? 'Reuse and request execution' : 'Generate and request execution'}</button></div>
    </form>
  </section></div>;
}

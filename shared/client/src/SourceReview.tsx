import type { GeneratedDraft } from '@janus/schema';

/** Generated source is a draft until the laptop validates and approves execution. */
export function SourceReview({ draft }: { draft: GeneratedDraft }) {
  const download = () => {
    const url = URL.createObjectURL(new Blob([draft.program.source], { type: 'text/x-c;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'janus-generated.c';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return <section aria-label="Generated source">
    <h3>{draft.program.summary}</h3>
    <p>This source has not been validated or executed.</p>
    <p>Target: {draft.program.platform} / {draft.program.architecture}</p>
    <p>Requested access: {draft.program.capabilities.join(', ') || 'none'}</p>
    <p>Libraries: {draft.program.dependencies.join(', ') || 'portable C only'}</p>
    <pre aria-label="C source" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}><code>{draft.program.source}</code></pre>
    <button type="button" onClick={download}>Download C source</button>
  </section>;
}

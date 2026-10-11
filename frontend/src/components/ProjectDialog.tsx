import { useState } from 'react';
import type { LogicalCanvasLayout } from '@janus/schema';
import { importProject, laptopProject } from '../editor/project';
import { createEmptyLayout } from '../editor/layout';

export function ProjectDialog({ layout, onReplace, onClose }: { layout: LogicalCanvasLayout; onReplace: (layout: LogicalCanvasLayout) => boolean; onClose: () => void }) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const replace = (next: LogicalCanvasLayout) => { if (onReplace(next)) onClose(); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(layout, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'janus-panel.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="project-heading">
    <h2 id="project-heading">Project</h2>
    <p>Replace the canvas with a starter panel or import a saved layout. Undo restores your previous canvas.</p>
    <p>The laptop starter uses locally enabled built-in controls. Screen sharing also requires permission on your laptop.</p>
    <button type="button" onClick={() => replace(laptopProject())}>Laptop controls starter</button>
    <button type="button" onClick={() => replace(createEmptyLayout())}>New empty project</button>
    <button type="button" onClick={download}>Export layout</button>
    <label>Import layout file<input type="file" accept="application/json,.json" onChange={async event => {
      const file = event.target.files?.[0]; if (!file) return;
      if (file.size > 262144) { setError('Project file must be under 256 KB.'); return; }
      try { setText(await file.text()); setError(''); } catch { setError('Could not read this file.'); }
    }} /></label>
    <label>Layout JSON<textarea aria-label="Layout JSON" value={text} onChange={event => setText(event.target.value)} /></label>
    <button type="button" disabled={!text.trim()} onClick={() => { try { replace(importProject(text)); } catch (reason) { setError(reason instanceof Error ? reason.message : 'Invalid project.'); } }}>Import layout</button>
    {error && <p role="alert">{error}</p>}
    <button type="button" onClick={onClose}>Close</button>
  </section></div>;
}

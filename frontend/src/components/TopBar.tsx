import { Icon } from '@janus/design'

export function TopBar({ name, connected, status, busy, onConnect, onPair, onSave, onHistory, onName, onProject }: {
  name: string; connected: boolean; status: string; busy: boolean;
  onConnect: () => void; onPair: () => void; onSave: () => void; onHistory: () => void;
  onName: (name: string) => void; onProject: () => void;
}) {
  return <header className="top-bar studio-glass">
    <div className="studio-brand"><img className="brand-mark" src="./janus-logo.svg" alt="JANUS" /><div><h1>Panel Builder</h1><span>JANUS STUDIO</span></div></div>
    <span className="toolbar-divider" aria-hidden="true" />
    <div className="project-name"><span>PROJECT</span><input aria-label="Project name" key={name} defaultValue={name} maxLength={80} disabled={busy} onBlur={event => { const next = event.target.value.trim(); if (next) onName(next); else event.target.value = name; }} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} /></div>
    <div className="toolbar-spacer" />
    <button className="studio-button" type="button" onClick={onProject} disabled={busy}>Project</button>
    <button className="studio-button" type="button" onClick={onConnect} disabled={busy}>{status}</button>
    <button className="studio-button" type="button" onClick={onHistory} disabled={!connected}>Requests</button>
    <button className="studio-button" type="button" onClick={onPair} disabled={!connected || busy}><Icon name="wifi" size={16} /><span>Pair Device</span></button>
    <button className="studio-button studio-button-primary" type="button" onClick={onSave} disabled={!connected || busy}><Icon name="save" size={16} /><span>Save &amp; Push</span></button>
  </header>
}

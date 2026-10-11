import { Icon } from '@janus/design'

export function TopBar({ name, connected, status, busy, onConnect, onPair, onSave, onHistory }: {
  name: string; connected: boolean; status: string; busy: boolean;
  onConnect: () => void; onPair: () => void; onSave: () => void; onHistory: () => void;
}) {
  return <header className="top-bar studio-glass">
    <div className="studio-brand"><span className="brand-mark"><Icon name="monitor" size={18} /></span><div><h1>Panel Builder</h1><span>STUDIO</span></div></div>
    <span className="toolbar-divider" aria-hidden="true" />
    <div className="project-name"><span>PROJECT</span><strong>{name}</strong></div>
    <div className="toolbar-spacer" />
    <button className="studio-button" type="button" onClick={onConnect}>{status}</button>
    <button className="studio-button" type="button" onClick={onHistory} disabled={!connected}>Requests</button>
    <button className="studio-button" type="button" onClick={onPair} disabled={!connected || busy}><Icon name="wifi" size={16} /><span>Pair Device</span></button>
    <button className="studio-button studio-button-primary" type="button" onClick={onSave} disabled={!connected || busy}><Icon name="save" size={16} /><span>Save &amp; Push</span></button>
  </header>
}

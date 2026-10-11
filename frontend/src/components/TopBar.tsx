import { Icon } from '@janus/design'

export function TopBar() {
  return <header className="top-bar studio-glass">
    <div className="studio-brand">
      <span className="brand-mark"><Icon name="monitor" size={18} /></span>
      <div><h1>Panel Builder</h1><span>STUDIO</span></div>
    </div>
    <span className="toolbar-divider" aria-hidden="true" />
    <div className="project-name"><span>PROJECT</span><strong>Untitled Project</strong></div>
    <div className="toolbar-spacer" />
    <div className="connection-indicator" aria-label="Not connected"><span /><span className="connection-label">Not connected</span></div>
    <button className="studio-button" type="button" disabled title="Device pairing is not implemented"><Icon name="wifi" size={16} /><span>Pair Device</span></button>
    <button className="studio-button studio-button-primary" type="button" disabled title="Save and Push is not implemented"><Icon name="save" size={16} /><span>Save &amp; Push</span></button>
  </header>
}

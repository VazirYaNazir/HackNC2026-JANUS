export function TopBar() {
  return <header className="top-bar glass-surface">
    <h1>Panel Builder</h1>
    <span className="toolbar-rule" aria-hidden="true" />
    <span className="project-name">Untitled Project</span>
    <button className="save-button" type="button" disabled>Save</button>
  </header>
}

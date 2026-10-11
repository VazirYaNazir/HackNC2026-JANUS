import { Icon } from '@janus/design'

export function CanvasToolbar() {
  return <div className="canvas-toolbar studio-glass" aria-label="Canvas status">
    <span className="canvas-indicator"><Icon name="phone" size={15} /><span>Phone</span></span>
    <span className="canvas-divider" aria-hidden="true" />
    <span className="canvas-indicator"><Icon name="grid" size={15} /><span>4-column grid</span></span>
    <span className="canvas-divider" aria-hidden="true" />
    <span className="canvas-indicator is-accent"><Icon name="edit" size={15} /><span>Edit mode</span></span>
  </div>
}

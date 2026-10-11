import { Icon } from '@janus/design'
import type { EditorMode } from '../editor/useEditorState'

export function CanvasToolbar({ snapToGrid, mode, canUndo, canRedo, onToggleSnap, onToggleMode, onUndo, onRedo }: {
  snapToGrid: boolean
  mode: EditorMode
  canUndo: boolean
  canRedo: boolean
  onToggleSnap: () => void
  onToggleMode: () => void
  onUndo: () => void
  onRedo: () => void
}) {
  return <div className="canvas-toolbar studio-glass" aria-label="Canvas status">
    <span className="canvas-indicator"><Icon name="phone" size={15} /><span>Phone</span></span>
    <span className="canvas-divider" aria-hidden="true" />
    <button className={`canvas-tool ${snapToGrid ? 'is-active' : ''}`} type="button" aria-pressed={snapToGrid} disabled={mode === 'preview'} onClick={onToggleSnap} title="Toggle four-column grid snapping"><Icon name="grid" size={15} /><span>{snapToGrid ? 'Grid on' : 'Free'}</span></button>
    <span className="canvas-divider" aria-hidden="true" />
    <button className="canvas-tool" type="button" disabled={!canUndo || mode === 'preview'} onClick={onUndo} title="Undo (Ctrl+Z)"><Icon name="undo" size={15} /><span className="sr-only">Undo</span></button>
    <button className="canvas-tool" type="button" disabled={!canRedo || mode === 'preview'} onClick={onRedo} title="Redo (Ctrl+Shift+Z)"><Icon name="redo" size={15} /><span className="sr-only">Redo</span></button>
    <span className="canvas-divider" aria-hidden="true" />
    <button className="canvas-tool is-accent" type="button" aria-pressed={mode === 'preview'} onClick={onToggleMode} title={`Switch to ${mode === 'edit' ? 'Preview' : 'Edit'} mode`}><Icon name={mode === 'edit' ? 'edit' : 'eye'} size={15} /><span>{mode === 'edit' ? 'Edit' : 'Preview'}</span></button>
  </div>
}

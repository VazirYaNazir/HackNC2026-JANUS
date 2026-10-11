import { CanvasToolbar } from './CanvasToolbar'
import { DevicePreview } from './DevicePreview'
import type { Rect } from '@janus/design'
import type { LogicalCanvasLayout, Widget, WidgetType } from '@janus/schema'
import type { WidgetValue } from '@janus/widgets'
import type { EditorMode } from '../editor/useEditorState'

export function EditorCanvas({ layout, selectedId, pendingType, snapToGrid, mode, values, data, states, canUndo, canRedo, onSelect, onInsert, onGeometryChange, onTrigger, onToggleSnap, onToggleMode, onUndo, onRedo }: {
  layout: LogicalCanvasLayout
  selectedId: string | null
  pendingType: WidgetType | null
  snapToGrid: boolean
  mode: EditorMode
  values: Record<string, WidgetValue>
  data?: Record<string, number | string | null>
  states?: Record<string, string>
  canUndo: boolean
  canRedo: boolean
  onSelect: (id: string | null) => void
  onInsert: (type: WidgetType, rect: Rect) => boolean
  onGeometryChange: (id: string, rect: Rect) => boolean
  onTrigger: (widget: Widget, value?: WidgetValue) => void
  onToggleSnap: () => void
  onToggleMode: () => void
  onUndo: () => void
  onRedo: () => void
}) {
  return <main className="editor-workspace" aria-label="Editor workspace">
    <CanvasToolbar snapToGrid={snapToGrid} mode={mode} canUndo={canUndo} canRedo={canRedo} onToggleSnap={onToggleSnap} onToggleMode={onToggleMode} onUndo={onUndo} onRedo={onRedo} />
    <div className="canvas-area"><DevicePreview layout={layout} selectedId={selectedId} pendingType={pendingType} snapToGrid={snapToGrid} mode={mode} values={values} data={data} states={states} onSelect={onSelect} onInsert={onInsert} onGeometryChange={onGeometryChange} onTrigger={onTrigger} /></div>
  </main>
}

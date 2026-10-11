import { useRef, useState, type DragEvent, type PointerEvent } from 'react'
import { gridSizeToLogical, logicalRectStyle, snapPositionToGrid, validatePlacement, type Rect } from '@janus/design'
import type { LogicalCanvasLayout, Widget, WidgetType } from '@janus/schema'
import { getWidgetDef, isInputWidget, WidgetView, type WidgetValue } from '@janus/widgets'
import { moveRect, resizeRect, viewportDeltaToLogical } from '../editor/geometry'
import type { EditorMode } from '../editor/useEditorState'

interface DevicePreviewProps {
  layout: LogicalCanvasLayout
  selectedId: string | null
  pendingType: WidgetType | null
  snapToGrid: boolean
  mode: EditorMode
  values: Record<string, WidgetValue>
  data?: Record<string, number | string | null>
  history?: Record<string, number[]>
  onSelect: (id: string | null) => void
  onInsert: (type: WidgetType, rect: Rect) => boolean
  onGeometryChange: (id: string, rect: Rect) => boolean
  onTrigger: (widget: Widget, value?: WidgetValue) => void
}

interface DraftGeometry {
  rect: Rect
  valid: boolean
}

interface Gesture {
  kind: 'move' | 'resize'
  pointerId: number
  clientX: number
  clientY: number
  origin: Rect
}

const rectOf = ({ x, y, w, h }: Widget): Rect => ({ x, y, w, h })
const sameRect = (a: Rect, b: Rect) => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h

function clientPoint(event: { clientX: number; clientY: number }, element: HTMLElement, layout: LogicalCanvasLayout) {
  const bounds = element.getBoundingClientRect()
  return {
    x: ((event.clientX - bounds.left) / bounds.width) * layout.canvas.width,
    y: ((event.clientY - bounds.top) / bounds.height) * layout.canvas.height,
  }
}

function EditorWidget({ widget, layout, selected, snapToGrid, mode, value, history, onSelect, onGeometryChange, onTrigger }: {
  widget: Widget
  layout: LogicalCanvasLayout
  selected: boolean
  snapToGrid: boolean
  mode: EditorMode
  value?: WidgetValue
  history?: number[]
  onSelect: (id: string) => void
  onGeometryChange: (id: string, rect: Rect) => boolean
  onTrigger: (widget: Widget, value?: WidgetValue) => void
}) {
  const [draft, setDraftState] = useState<DraftGeometry | null>(null)
  const gesture = useRef<Gesture | null>(null)
  const draftRef = useRef<DraftGeometry | null>(null)
  const displayRect = draft?.rect ?? rectOf(widget)

  const setDraft = (next: DraftGeometry | null) => {
    draftRef.current = next
    setDraftState(next)
  }

  function startGesture(event: PointerEvent<HTMLDivElement | HTMLButtonElement>, kind: Gesture['kind']) {
    if (mode !== 'edit' || event.button !== 0) return
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    onSelect(widget.id)
    gesture.current = {
      kind,
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      origin: rectOf(widget),
    }
    setDraft({ rect: rectOf(widget), valid: true })
  }

  function moveGesture(event: PointerEvent<HTMLDivElement | HTMLButtonElement>) {
    const current = gesture.current
    if (!current || current.pointerId !== event.pointerId) return
    const canvas = event.currentTarget.closest<HTMLElement>('.phone-canvas')
    if (!canvas) return
    const bounds = canvas.getBoundingClientRect()
    const delta = viewportDeltaToLogical(
      { x: event.clientX - current.clientX, y: event.clientY - current.clientY },
      { width: bounds.width, height: bounds.height },
      layout.canvas,
    )
    const rect = current.kind === 'move'
      ? moveRect(current.origin, delta, snapToGrid)
      : resizeRect(widget.type, current.origin, delta, snapToGrid)

    const validation = validatePlacement(rect, layout.widgets, widget.id, layout.canvas)
    setDraft({ rect, valid: validation.valid })
  }

  function finishGesture(event: PointerEvent<HTMLDivElement | HTMLButtonElement>) {
    const current = gesture.current
    if (!current || current.pointerId !== event.pointerId) return
    gesture.current = null
    const candidate = draftRef.current
    if (candidate?.valid && !sameRect(candidate.rect, current.origin)) onGeometryChange(widget.id, candidate.rect)
    setDraft(null)
  }

  const interactionHandlers = {
    onPointerMove: moveGesture,
    onPointerUp: finishGesture,
    onPointerCancel: finishGesture,
  }

  return <div
    className={`editor-widget ${selected && mode === 'edit' ? 'is-selected' : ''} ${draft && !draft.valid ? 'is-invalid' : ''}`}
    style={logicalRectStyle(displayRect, layout.canvas)}
    onPointerDown={(event) => startGesture(event, 'move')}
    {...interactionHandlers}
  >
    <WidgetView
      widget={widget}
      mode={mode === 'preview' ? 'live' : 'edit'}
      value={value}
      history={history}
      selected={selected && mode === 'edit'}
      onSelect={mode === 'edit' ? onSelect : undefined}
      onTrigger={mode === 'preview' ? onTrigger : undefined}
    />
    {selected && mode === 'edit' && <button
      className="resize-handle"
      type="button"
      aria-label={`Resize ${widget.label}`}
      onPointerDown={(event) => startGesture(event, 'resize')}
      {...interactionHandlers}
    />}
  </div>
}

export function DevicePreview({ layout, selectedId, pendingType, snapToGrid, mode, values, data = {}, history = {}, onSelect, onInsert, onGeometryChange, onTrigger }: DevicePreviewProps) {
  const [placement, setPlacement] = useState<DraftGeometry | null>(null)

  function proposedPlacement(event: DragEvent<HTMLDivElement>): DraftGeometry | null {
    if (!pendingType || mode !== 'edit') return null
    const definition = getWidgetDef(pendingType)
    if (!definition) return null
    const point = clientPoint(event, event.currentTarget, layout)
    const size = gridSizeToLogical(definition.defaultSize.w, definition.defaultSize.h)
    let rect: Rect = { x: point.x - size.w / 2, y: point.y - size.h / 2, ...size }
    if (snapToGrid) rect = snapPositionToGrid(rect)
    return { rect, valid: validatePlacement(rect, layout.widgets, undefined, layout.canvas).valid }
  }

  function dragOver(event: DragEvent<HTMLDivElement>) {
    const next = proposedPlacement(event)
    if (!next) return
    event.preventDefault()
    event.dataTransfer.dropEffect = next.valid ? 'copy' : 'none'
    setPlacement(next)
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    const next = proposedPlacement(event)
    if (next?.valid && pendingType) onInsert(pendingType, next.rect)
    setPlacement(null)
  }

  return <div className="phone-frame" aria-label="Phone preview">
    <div
      className={`phone-screen phone-canvas ${mode === 'preview' ? 'is-preview' : 'is-editing'} ${snapToGrid ? 'is-snapping' : ''}`}
      onClick={(event) => { if (mode === 'edit' && event.target === event.currentTarget) onSelect(null) }}
      onDragOver={dragOver}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setPlacement(null) }}
      onDrop={drop}
    >
      {layout.widgets.map((widget) => <EditorWidget
        key={widget.id}
        widget={widget}
        layout={layout}
        selected={selectedId === widget.id}
        snapToGrid={snapToGrid}
        mode={mode}
        value={isInputWidget(widget) ? (widget.action === 'native_volume' ? data['volume.percent'] ?? values[widget.id] : values[widget.id]) : data[widget.source] ?? undefined}
        history={isInputWidget(widget) ? undefined : history[widget.source]}
        onSelect={onSelect}
        onGeometryChange={onGeometryChange}
        onTrigger={onTrigger}
      />)}
      {placement && pendingType && mode === 'edit' && <div className={`placement-preview ${placement.valid ? 'is-valid' : 'is-invalid'}`} style={logicalRectStyle(placement.rect, layout.canvas)} />}
    </div>
  </div>
}

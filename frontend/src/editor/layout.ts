import { LOGICAL_CANVAS, validatePlacement, type Rect } from '@janus/design'
import { SCHEMA_VERSION, type LogicalCanvasLayout, type Widget } from '@janus/schema'

export type LayoutMutation =
  | { ok: true; layout: LogicalCanvasLayout }
  | { ok: false; message: string }

export function createEmptyLayout(name = 'Untitled Project'): LogicalCanvasLayout {
  return {
    schemaVersion: SCHEMA_VERSION,
    coordinateSpace: 'logical',
    canvas: { ...LOGICAL_CANVAS },
    name,
    widgets: [],
  }
}

export function nextWidgetId(type: string, widgets: readonly Widget[]): string {
  const used = new Set(widgets.map(({ id }) => id))
  let sequence = 1
  while (used.has(`${type}-${sequence}`)) sequence += 1
  return `${type}-${sequence}`
}

function reasonMessage(reason: string | undefined, collidingId?: string): string {
  if (reason === 'collision') return `Widget overlaps “${collidingId}”.`
  if (reason === 'out-of-bounds') return 'Widget must remain inside the 360 × 780 canvas.'
  return 'Widget geometry must contain finite, positive dimensions.'
}

export function insertWidget(layout: LogicalCanvasLayout, widget: Widget): LayoutMutation {
  const validation = validatePlacement(widget, layout.widgets, undefined, layout.canvas)
  if (!validation.valid) return { ok: false, message: reasonMessage(validation.reason, validation.collidingId) }
  if (layout.widgets.some(({ id }) => id === widget.id)) return { ok: false, message: `Widget ID “${widget.id}” already exists.` }
  return { ok: true, layout: { ...layout, widgets: [...layout.widgets, widget] } }
}

export function updateWidgetGeometry(layout: LogicalCanvasLayout, id: string, rect: Rect): LayoutMutation {
  const validation = validatePlacement(rect, layout.widgets, id, layout.canvas)
  if (!validation.valid) return { ok: false, message: reasonMessage(validation.reason, validation.collidingId) }
  let found = false
  const widgets = layout.widgets.map((widget) => {
    if (widget.id !== id) return widget
    found = true
    return { ...widget, ...rect }
  })
  return found ? { ok: true, layout: { ...layout, widgets } } : { ok: false, message: `Widget “${id}” was not found.` }
}

export function updateWidget(layout: LogicalCanvasLayout, id: string, update: (widget: Widget) => Widget): LayoutMutation {
  let found = false
  const widgets = layout.widgets.map((widget) => {
    if (widget.id !== id) return widget
    found = true
    return update(widget)
  })
  return found ? { ok: true, layout: { ...layout, widgets } } : { ok: false, message: `Widget “${id}” was not found.` }
}

export function deleteWidget(layout: LogicalCanvasLayout, id: string): LayoutMutation {
  if (!layout.widgets.some((widget) => widget.id === id)) return { ok: false, message: `Widget “${id}” was not found.` }
  return { ok: true, layout: { ...layout, widgets: layout.widgets.filter((widget) => widget.id !== id) } }
}

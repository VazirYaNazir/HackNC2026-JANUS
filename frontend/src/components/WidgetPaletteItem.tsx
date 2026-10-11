import { Icon } from '@janus/design'
import type { WidgetDef } from '@janus/widgets'
import type { DragEvent } from 'react'
import type { WidgetType } from '@janus/schema'

export function WidgetPaletteItem({ type, definition, disabled, onDragStart, onDragEnd }: {
  type: WidgetType
  definition: WidgetDef
  disabled: boolean
  onDragStart: (type: WidgetType) => void
  onDragEnd: () => void
}) {
  function startDrag(event: DragEvent<HTMLDivElement>) {
    if (disabled) {
      event.preventDefault()
      return
    }
    event.dataTransfer.effectAllowed = 'copy'
    event.dataTransfer.setData('application/x-panel-widget', type)
    onDragStart(type)
  }

  return <div className="palette-item" role="listitem" tabIndex={disabled ? -1 : 0} draggable={!disabled} aria-disabled={disabled} aria-label={`${definition.label} widget`} onDragStart={startDrag} onDragEnd={onDragEnd}>
    <span className="palette-item-icon"><Icon name={definition.icon} size={20} /></span>
    <span className="palette-item-copy"><strong>{definition.label}</strong><small>{definition.defaultSize.w} × {definition.defaultSize.h} grid</small></span>
    <span className="palette-type">{type}</span>
  </div>
}

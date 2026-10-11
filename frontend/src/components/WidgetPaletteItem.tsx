import { Icon } from '@janus/design'
import type { WidgetDef } from '@janus/widgets'

export function WidgetPaletteItem({ type, definition }: { type: string; definition: WidgetDef }) {
  return <div className="palette-item" role="listitem" tabIndex={0} aria-label={`${definition.label} widget, placement unavailable`}>
    <span className="palette-item-icon"><Icon name={definition.icon} size={20} /></span>
    <span className="palette-item-copy"><strong>{definition.label}</strong><small>{definition.defaultSize.w} × {definition.defaultSize.h} grid</small></span>
    <span className="palette-type">{type}</span>
  </div>
}

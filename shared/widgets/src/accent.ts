import { isInput, type Widget } from '@janus/schema';

/** Match the inspector, newly created widgets, and colorless saved layouts. */
export function getWidgetAccent(widget: Pick<Widget, 'type' | 'color'>): NonNullable<Widget['color']> {
  return widget.color ?? (widget.type === 'toggle' ? 'green' : isInput(widget.type) ? 'amber' : 'cyan');
}

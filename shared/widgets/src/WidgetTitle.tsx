import { Icon } from "@janus/design";
import type { Widget } from "@janus/schema";

/** Icon + one-line label. The label ellipsizes, and is hidden only by container queries (icon-only). */
export default function WidgetTitle({ widget }: { widget: Widget }) {
  return (
    <span className="jw__title jw__heading" title={widget.label}>
      {widget.icon && <Icon name={widget.icon} className="jw__ico" />}
      <span className="jw__label">{widget.label}</span>
    </span>
  );
}

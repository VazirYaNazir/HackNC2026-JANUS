import { Icon } from "@janus/design";
import type { Widget } from "@janus/schema";

export default function WidgetTitle({ widget }: { widget: Widget }) {
  return (
    <span className="jw__title jw__heading">
      {widget.icon && <Icon name={widget.icon} size={16} />}
      {widget.label}
    </span>
  );
}

import type { ReactNode } from "react";
import type { WidgetProps } from "./types";
import "./widgets.css";

interface FrameProps {
  props: WidgetProps;
  className?: string;
  /** Non-button widgets get a select overlay in edit mode so they stay keyboard/touch selectable. */
  selectable?: boolean;
  children: ReactNode;
}

/**
 * Widget root and size container (.jw). Container queries can only style descendants, so the layout
 * lives on the inner .jw__body.
 */
export function Frame({ props, className = "", selectable = false, children }: FrameProps) {
  const { widget, mode, selected, onSelect } = props;
  const classes = ["jw", `jw--${widget.type}`, widget.icon && "jw--icon", selected && "jw--selected", className];
  return (
    <div className={classes.filter(Boolean).join(" ")}>
      <div className="jw__body">{children}</div>
      {selectable && mode === "edit" && (
        <button
          type="button"
          className="jw__select"
          aria-label={`Select ${widget.label}`}
          onClick={() => onSelect?.(widget.id)}
        />
      )}
    </div>
  );
}

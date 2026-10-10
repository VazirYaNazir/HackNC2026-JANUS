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

export function Frame({ props, className = "", selectable = false, children }: FrameProps) {
  const { widget, mode, selected, onSelect } = props;
  return (
    <div className={`jw jw--${widget.type} ${selected ? "jw--selected" : ""} ${className}`}>
      {children}
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

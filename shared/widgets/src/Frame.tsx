import { useLayoutEffect, useRef, type CSSProperties, type ReactNode } from "react";
import type { WidgetProps } from "./types";
import { getWidgetAccent } from './accent';
import "./widgets.css";

interface FrameProps {
  props: WidgetProps;
  className?: string;
  /** Overrides the `jw--<type>` class, for a variant that borrows another type's layout. */
  typeClass?: string;
  /** Non-button widgets get a select overlay in edit mode so they stay keyboard/touch selectable. */
  selectable?: boolean;
  children: ReactNode;
}

/**
 * Widget root and size container (.jw). Container queries can only style descendants, so the layout
 * lives on the inner .jw__body.
 */
export function Frame({ props, className = "", typeClass, selectable = false, children }: FrameProps) {
  const { widget, mode, selected, onSelect } = props;
  const rootRef = useRef<HTMLDivElement>(null);

  // Flags the widget when its label is truncated, so the 1x1 layouts can swap the label for an icon/value.
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const measure = () => {
      const label = root.querySelector<HTMLElement>(".jw__label");
      if (label) root.toggleAttribute("data-clipped", label.scrollWidth > label.clientWidth + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    void document.fonts?.ready.then(measure);
    return () => observer.disconnect();
  }, [widget.label, widget.icon]);

  const classes = ["jw", `jw--${typeClass ?? widget.type}`, widget.icon && "jw--icon", selected && "jw--selected", mode === "live" && "jw--live", className];
  return (
    <div
      ref={rootRef}
      className={classes.filter(Boolean).join(" ")}
      style={{ "--jw-accent": `var(--${getWidgetAccent(widget)})` } as CSSProperties}
    >
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

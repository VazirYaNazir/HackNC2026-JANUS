import { useEffect, useRef } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import type { WidgetProps } from "./types";
import { useHistory } from "./useHistory";

const MAX_LINES = 50;
const STICK_THRESHOLD = 24;

export default function LogFeedWidget(props: WidgetProps) {
  const { widget, value } = props;
  const lines = useHistory(typeof value === "string" ? value : undefined, MAX_LINES);
  const boxRef = useRef<HTMLDivElement>(null);
  const stuck = useRef(true);

  // Follow the newest line, unless the user has scrolled up to read.
  useEffect(() => {
    const box = boxRef.current;
    if (box && stuck.current) box.scrollTop = box.scrollHeight;
  }, [lines]);

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <div
        ref={boxRef}
        className="jw__logbox"
        role="log"
        aria-live="off"
        aria-label={widget.label}
        tabIndex={0}
        onScroll={(e) => {
          const box = e.currentTarget;
          stuck.current = box.scrollHeight - box.scrollTop - box.clientHeight < STICK_THRESHOLD;
        }}
      >
        {lines.map((line, i) => (
          <div key={i} className="jw__logline">
            {line}
          </div>
        ))}
      </div>
    </Frame>
  );
}

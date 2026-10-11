import { useState } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import type { WidgetProps } from "./types";

type Status = "waiting" | "live" | "offline";

/**
 * Shows a stream from the host in an inset screen. `value` is the current frame's image URL: an MJPEG
 * endpoint (the browser keeps it playing) or a frame URL that changes as new frames arrive.
 */
export default function StreamWidget(props: WidgetProps) {
  const { widget, value, mode } = props;
  const src = typeof value === "string" && value !== "" ? value : undefined;
  // The URL that failed to load; a different URL gets a fresh try.
  const [failed, setFailed] = useState<string | undefined>();

  const status: Status = !src ? "waiting" : failed === src ? "offline" : "live";
  const text = status === "waiting" ? "Waiting for stream…" : "Stream offline";

  return (
    <Frame props={props} selectable>
      <div className="jw__row">
        <WidgetTitle widget={widget} />
        <span className={`jw__live jw__live--${status}`}>{status === "live" ? "LIVE" : status === "offline" ? "OFF" : "…"}</span>
      </div>
      <div className="jw__screen" role="img" aria-label={`${widget.label}: ${status}`}>
        {status === "live" ? (
          <img
            src={src}
            alt=""
            draggable={false}
            // In edit mode the picture is only a preview.
            className={mode === "edit" ? "jw__frame jw__frame--dim" : "jw__frame"}
            onError={() => setFailed(src)}
          />
        ) : (
          <span className="jw__nosignal">{text}</span>
        )}
      </div>
    </Frame>
  );
}

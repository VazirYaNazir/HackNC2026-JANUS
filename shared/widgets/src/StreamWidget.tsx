import { useState } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import type { WidgetProps } from "./types";

type Status = "waiting" | "loading" | "live" | "offline";

/**
 * Shows a stream from the host in an inset screen. `value` is the current frame's image URL: an MJPEG
 * endpoint (the browser keeps it playing) or a frame URL that changes as new frames arrive.
 */
export default function StreamWidget(props: WidgetProps) {
  const { widget, value, mode } = props;
  const src = typeof value === "string" && value !== "" ? value : undefined;
  // The URL that failed to load; a different URL gets a fresh try.
  const [failed, setFailed] = useState<string | undefined>();
  const [loaded, setLoaded] = useState<string | undefined>();
  const captureError = props.telemetryStatus && props.telemetryStatus !== 'Live' ? props.telemetryStatus : undefined;

  const status: Status = captureError || failed === src && src ? 'offline' : !src ? 'waiting' : loaded === src ? 'live' : 'loading';
  const text = captureError ?? (status === 'waiting' ? 'Waiting for stream…' : status === 'loading' ? 'Loading screen…' : 'Stream offline');

  return (
    <Frame props={props} selectable>
      <div className="jw__row">
        <WidgetTitle widget={widget} />
        <span className={`jw__live jw__live--${status}`}>{status === "live" ? "LIVE" : status === "offline" ? "OFF" : "…"}</span>
      </div>
      <div className="jw__screen" role="img" aria-label={`${widget.label}: ${status}`}>
        {src && status !== 'offline' && (
          <img
            src={src}
            alt=""
            draggable={false}
            // In edit mode the picture is only a preview.
            className={mode === "edit" ? "jw__frame jw__frame--dim" : "jw__frame"}
            onError={() => setFailed(src)}
            onLoad={() => { setLoaded(src); setFailed(undefined); }}
          />
        )}
        {status !== 'live' && (
          <span className="jw__nosignal">{text}</span>
        )}
      </div>
    </Frame>
  );
}

import { useCallback, useEffect, useRef } from "react";

/**
 * Rate-limits a continuous stream (a dragged joystick) to one send per `intervalMs`.
 * `push` sends right away if the interval has passed, otherwise it keeps only the latest value and
 * sends it when the interval ends. `sendNow` cancels anything pending and sends immediately (release).
 */
export function useThrottledSend<T>(send: (value: T) => void, intervalMs: number) {
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });
  const lastAt = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pending = useRef<{ value: T } | null>(null);

  const push = useCallback(
    (value: T) => {
      const wait = intervalMs - (performance.now() - lastAt.current);
      if (wait <= 0 && timer.current === undefined) {
        lastAt.current = performance.now();
        sendRef.current(value);
        return;
      }
      pending.current = { value };
      if (timer.current === undefined) {
        timer.current = setTimeout(() => {
          timer.current = undefined;
          const latest = pending.current;
          pending.current = null;
          if (latest) {
            lastAt.current = performance.now();
            sendRef.current(latest.value);
          }
        }, Math.max(wait, 0));
      }
    },
    [intervalMs],
  );

  const sendNow = useCallback((value: T) => {
    clearTimeout(timer.current);
    timer.current = undefined;
    pending.current = null;
    lastAt.current = performance.now();
    sendRef.current(value);
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  return { push, sendNow };
}

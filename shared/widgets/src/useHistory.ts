import { useState } from "react";

/**
 * Keeps the last `max` samples. A sample is appended each time `sample` changes, so the widget
 * only needs the latest value as a prop. Identical consecutive samples count as no change.
 */
export function useHistory<T>(sample: T | undefined, max: number): T[] {
  const [state, setState] = useState<{ last: T | undefined; items: T[] }>(() => ({
    last: sample,
    items: sample === undefined ? [] : [sample],
  }));
  if (sample !== undefined && sample !== state.last) {
    const next = { last: sample, items: [...state.items, sample].slice(-max) };
    setState(next);
    return next.items;
  }
  return state.items;
}

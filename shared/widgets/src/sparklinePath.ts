/** SVG path for a sparkline of `values` in a `width` x `height` box, oldest value on the left. Empty when it cannot draw. */
export function sparklinePath(values: readonly number[], width: number, height: number, padding = 2): string {
  const points = values.filter((value) => Number.isFinite(value));
  if (points.length < 2) return "";
  const low = Math.min(...points);
  const high = Math.max(...points);
  const span = high - low;
  const innerHeight = Math.max(0, height - padding * 2);
  const step = width / (points.length - 1);
  return points
    .map((value, index) => {
      const x = index * step;
      // A flat series sits in the middle instead of dividing by zero.
      const y = span === 0 ? height / 2 : padding + innerHeight * (1 - (value - low) / span);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

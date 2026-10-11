import { sparklinePath } from "./sparklinePath";

const WIDTH = 60;
const HEIGHT = 14;

/** A last-hour trend under a gauge. Decorative: the gauge's own label carries the reading. */
export default function Sparkline({ values }: { values?: readonly number[] }) {
  const path = values ? sparklinePath(values, WIDTH, HEIGHT) : "";
  if (!path) return null;
  return (
    <svg className="jw__spark" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path d={path} />
    </svg>
  );
}

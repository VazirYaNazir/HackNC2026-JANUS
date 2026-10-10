/* 270° sweep on a 100x100 box, open at the bottom. f runs 0..1 from bottom-left, clockwise. */
const C = 50;

export const polar = (r: number, f: number) => {
  const a = ((225 - f * 270) * Math.PI) / 180;
  return { x: C + r * Math.cos(a), y: C - r * Math.sin(a) };
};

/** Path for the full 270° sweep at radius `r`. Pair with pathLength={100} to fill by percentage. */
export function sweepPath(r: number): string {
  const start = polar(r, 0);
  const end = polar(r, 1);
  return `M ${start.x} ${start.y} A ${r} ${r} 0 1 1 ${end.x} ${end.y}`;
}

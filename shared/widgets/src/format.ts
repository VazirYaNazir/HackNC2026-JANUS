export const asNumber = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

/** Whole numbers as-is, otherwise one decimal. */
export const formatNumber = (n: number): string => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));

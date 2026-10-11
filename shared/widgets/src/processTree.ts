export interface ProcessRow { pid: number; parent_pid: number | null; name: string; context: boolean; cpu_percent: number | null; ram_bytes: number | null; depth: number }

/** Bounded, cycle-safe indentation, including parents that exist only as context. */
export function processRows(value: unknown): ProcessRow[] | null {
  if (typeof value !== 'string') return null;
  try {
    const raw: unknown = JSON.parse(value);
    if (!Array.isArray(raw) || raw.length > 512) return null;
    const byId = new Map<number, ProcessRow>();
    for (const p of raw) {
      if (!p || !Number.isInteger(p.pid) || p.pid < 0 || byId.has(p.pid) || typeof p.name !== 'string' || typeof p.context !== 'boolean'
        || p.parent_pid !== null && (!Number.isInteger(p.parent_pid) || p.parent_pid < 0)
        || [p.cpu_percent, p.ram_bytes].some(v => v !== null && (typeof v !== 'number' || !Number.isFinite(v) || v < 0))) return null;
      byId.set(p.pid, { ...p, depth: 0 });
    }
    const rows: ProcessRow[] = [], visited = new Set<number>();
    const children = new Map<number | null, ProcessRow[]>();
    for (const p of byId.values()) {
      const parent = byId.has(p.parent_pid!) && p.parent_pid !== p.pid ? p.parent_pid : null;
      children.set(parent, [...children.get(parent) ?? [], p]);
    }
    const visit = (p: ProcessRow, depth: number) => {
      if (visited.has(p.pid)) return;
      visited.add(p.pid); rows.push({ ...p, depth: Math.min(depth, 12) });
      for (const child of children.get(p.pid) ?? []) visit(child, depth + 1);
    };
    for (const root of children.get(null) ?? []) visit(root, 0);
    for (const p of byId.values()) visit(p, 0);
    return rows;
  } catch { return null; }
}

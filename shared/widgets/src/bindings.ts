import type { DialType, InputType, Widget } from '@janus/schema';

export const NUMERIC_SOURCES = ['cpu.percent', 'mem.percent', 'mem.used_mb', 'cpu.temperature', 'volume.percent'];
export const STATUS_SOURCES = ['native.metrics.status', 'native.screen.status', 'native.processes.status', 'native.volume.status', 'native.mouse.status'];

export function builtinSources(type: DialType): readonly string[] {
  return type === 'stream' ? ['screen.frame'] : type === 'processes' ? ['process.tree'] : type === 'log' ? STATUS_SOURCES : NUMERIC_SOURCES;
}

export function builtinActions(type: InputType): readonly string[] {
  return type === 'slider' || type === 'knob' ? ['native_volume', 'native_scroll'] : type === 'joystick' ? ['native_mouse'] : type === 'button' ? ['native_left_click', 'native_right_click'] : [];
}

export function sourceUnit(source: string): string | undefined {
  return source.endsWith('.percent') ? '%' : source === 'mem.used_mb' ? 'MB' : source === 'cpu.temperature' ? '°C' : undefined;
}

/** Bind native-capable palette widgets to actual publishers; generated controls stay explicitly unconfigured. */
export function defaultBinding(type: Widget['type']): { action: string } | { source: string; unit?: string } {
  if (type === 'slider' || type === 'knob') return { action: 'native_volume' };
  if (type === 'joystick') return { action: 'native_mouse' };
  if (['button', 'toggle', 'dpad', 'command', 'agent'].includes(type)) return { action: '' };
  const source = type === 'stream' ? 'screen.frame' : type === 'processes' ? 'process.tree' : type === 'log' ? 'native.metrics.status'
    : type === 'temperature' ? 'cpu.temperature' : type === 'number' ? 'mem.used_mb' : type === 'bar' || type === 'ring' ? 'mem.percent' : 'cpu.percent';
  return { source, unit: sourceUnit(source) };
}

/** Additional device status accompanies image/process/temperature readings; it never substitutes for data. */
export function telemetryStatus(widget: Widget, data: Record<string, number | string | null>): string | undefined {
  if (!('source' in widget)) return undefined;
  const stream = widget.source === 'screen.frame' ? 'screen' : widget.source === 'process.tree' ? 'processes' : widget.source === 'cpu.temperature' ? 'metrics' : undefined;
  const value = stream ? data[`native.${stream}.status`] : undefined;
  return typeof value === 'string' ? value : undefined;
}

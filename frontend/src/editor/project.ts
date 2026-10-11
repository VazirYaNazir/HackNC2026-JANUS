import { normalizeLayout } from '@janus/design';
import { parsePanelDocument, type LogicalCanvasLayout } from '@janus/schema';
import { WIDGET_TYPES } from '@janus/widgets';

export function validateProject(layout: LogicalCanvasLayout): LogicalCanvasLayout {
  parsePanelDocument({ layout, revision: 0 });
  if (!layout.name.trim() || layout.name.length > 80 || layout.widgets.length > 64) throw new Error('Use a project name of 1–80 characters and at most 64 widgets.');
  for (const w of layout.widgets) {
    if (!WIDGET_TYPES.includes(w.type)) throw new Error(`Unsupported widget type: ${w.type}.`);
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(w.id) || !w.label.trim() || w.label.length > 128) throw new Error('Use valid widget IDs and labels of 1–128 characters.');
    if ('action' in w && !/^[A-Za-z0-9_-]{0,128}$/.test(w.action)) throw new Error('Action IDs may contain letters, digits, underscores and hyphens.');
    if ('source' in w && (w.source.length > 128 || (w.unit?.length ?? 0) > 32)) throw new Error('Source IDs must be at most 128 characters and units at most 32.');
    if ('min' in w || 'max' in w || 'step' in w) {
      if ('action' in w && (w.type === 'slider' || w.type === 'knob') && (w.min ?? 0) >= (w.max ?? 100)) throw new Error('Minimum must be smaller than maximum.');
      if ('step' in w && w.step !== undefined && w.step <= 0) throw new Error('Step must be greater than zero.');
    }
    if ('warn' in w && 'critical' in w && w.warn !== undefined && w.critical !== undefined && w.warn > w.critical) throw new Error('Warning threshold must not exceed critical threshold.');
  }
  return normalizeLayout(layout);
}

export function importProject(text: string): LogicalCanvasLayout {
  if (text.length > 262144) throw new Error('Project file must be under 256 KB.');
  const raw: unknown = JSON.parse(text);
  const document = parsePanelDocument({ layout: raw, revision: 0 });
  return validateProject(normalizeLayout(document.layout));
}

export function laptopProject(): LogicalCanvasLayout {
  return { schemaVersion: 2, coordinateSpace: 'logical', canvas: { width: 360, height: 780 }, name: 'Laptop Controls', widgets: [
    { id: 'volume', type: 'slider', label: 'Volume', action: 'native_volume', icon: 'volume', x: 0, y: 0, w: 360, h: 70, min: 0, max: 100, step: 1 },
    { id: 'mouse', type: 'joystick', label: 'Mouse', action: 'native_mouse', x: 0, y: 82, w: 170, h: 170 },
    { id: 'left-click', type: 'button', label: 'Left click', action: 'native_left_click', x: 190, y: 82, w: 170, h: 70 },
    { id: 'right-click', type: 'button', label: 'Right click', action: 'native_right_click', x: 190, y: 164, w: 170, h: 70 },
    { id: 'ram', type: 'bar', label: 'RAM', source: 'mem.percent', unit: '%', x: 0, y: 264, w: 170, h: 70 },
    { id: 'temperature', type: 'temperature', label: 'CPU temperature', source: 'cpu.temperature', unit: '°C', x: 190, y: 264, w: 170, h: 70 },
    { id: 'processes', type: 'processes', label: 'Relevant processes', source: 'process.tree', x: 0, y: 346, w: 360, h: 200 },
    { id: 'screen', type: 'stream', label: 'Laptop screen', source: 'screen.frame', x: 0, y: 558, w: 360, h: 210 },
  ] };
}

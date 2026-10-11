export const LEGACY_SCHEMA_VERSION = 1 as const;
export const SCHEMA_VERSION = 2 as const;

/* ---------- Layout (GET /api/layout) ---------- */

export type InputType = "button" | "toggle" | "slider" | "joystick" | "dpad" | "knob" | "command" | "agent";
export type DialType = "gauge" | "bar" | "line" | "ring" | "temperature" | "number" | "log" | "stream";
export type WidgetType = InputType | DialType;

/** Joystick position, each axis -1..1 (y is up). */
export interface Vec2 { x: number; y: number; }

/**
 * What an input sends when triggered: boolean (toggle), number (slider, knob), string (command text,
 * agent prompt, d-pad direction), Vec2 (joystick). Buttons send nothing.
 */
export type WidgetValue = number | string | boolean | Vec2;

export const INPUT_TYPES: readonly InputType[] = ["button", "toggle", "slider", "joystick", "dpad", "knob", "command", "agent"];
export const isInput = (t: WidgetType): t is InputType => (INPUT_TYPES as readonly string[]).includes(t);

export interface WidgetBase {
  id: string;            // "w1"
  type: WidgetType;
  label: string;         // "Run Backup"
  icon?: string;         // built-in name ("lock") or "asset:<id>"
  color?: "amber" | "cyan" | "green" | "graphite" | "coral";
  /** Geometry units are defined by the enclosing layout's schema version. */
  x: number; y: number;
  w: number; h: number;
}

export interface InputWidget extends WidgetBase {
  type: InputType;
  action: string;        // action ID only; the phone never sees what it runs
  confirm?: boolean;     // PC must approve before running
  min?: number; max?: number; step?: number; // slider / knob
  variant?: "switch" | "push"; // toggle: sliding switch (default) or a button that stays pushed in while on
}

export interface DialWidget extends WidgetBase {
  type: DialType;
  source: string;        // "cpu.percent", "mem.percent", …
  unit?: string;         // "%", "°C", "MB/s"
  warn?: number;         // threshold for amber
  critical?: number;     // threshold for coral
}

export type Widget = InputWidget | DialWidget;

export interface LegacyGridLayout {
  schemaVersion: typeof LEGACY_SCHEMA_VERSION;
  name: string;          // "Desk Control"
  columns: number;       // 4 on phone
  widgets: Widget[];
}

export interface LogicalCanvasLayout {
  schemaVersion: typeof SCHEMA_VERSION;
  coordinateSpace: "logical";
  canvas: {
    width: number;
    height: number;
  };
  name: string;
  widgets: Widget[];
}

export type Layout = LegacyGridLayout | LogicalCanvasLayout;

/** What GET /api/layout returns: the saved layout and its revision (it increases on every save). */
export interface PanelDocument { layout: Layout; revision: number }

/* ---------- Pairing (POST /api/pair) ---------- */

export interface PairRequest { code: string; name: string; }      // name: "Ana's iPhone"
export interface PairResponse { token: string; deviceId: string; agentDeviceId: string; pcName: string; }

/** A pairing link, parsed. The PC's QR code encodes `<host>?pairing_code=<6 hex characters>`. */
export interface PairQR { host: string; code: string; }          // host: "https://abc.trycloudflare.com"

/* ---------- WebSocket (/ws) ---------- */

// Phone → PC
export type ClientMessage =
  | { type: "auth"; token: string }                                   // must be first
  | { type: "ping" }
  | { type: "trigger"; widget: string; action: string; job_id: string; value?: WidgetValue; program_id?: string; request_sha256?: string };

// PC → phone
export type RunState = "running" | "done" | "error";

export type ServerMessage =
  | { type: "auth_ok" }
  | { type: "status"; widget: string; action: string; state: RunState; output?: string }
  | { type: "confirm_pending"; widget: string; action: string }      // waiting for approval on the PC
  | { type: "data"; source: string; value: number | string }
  | { type: "layout_updated" }
  | { type: "pong" }
  | { type: "job_update"; job: JobView }
  | { type: "agent_presence"; session: { device_id: string; online: boolean; target: { platform: GenerationRequest['platform']; architecture: GenerationRequest['architecture']; build_boundary_available: boolean; execution_boundary_available: boolean } | null } }
  | { type: "error"; message: string };

/* ---------- Saved on the phone (localStorage) ---------- */

/** A paired PC panel shown on the phone's home screen. */
export interface SavedPanel {
  id: string;
  name: string;          // user-editable, "Desk Control"
  pcName: string;        // "my-pc"
  host: string;
  token: string;
  agentDeviceId?: string;
  addedAt: number;       // epoch ms
  lastOpenedAt?: number; // epoch ms
}

export interface GenerationRequest {
  description: string;
  operation: string;
  platform: "Darwin" | "Windows";
  architecture: "arm64" | "x86_64" | "x86";
  capabilities: string[];
  dependencies: string[];
  output_protocol: "text" | "json-v1";
}

export interface JobView {
  job_id: string;
  device_id: string;
  state: string;
  reason: string | null;
  result: { stdout?: string; stderr?: string; status?: string; reason?: string | null } | null;
  program_id?: string | null;
  widget_id?: string | null;
  action_id?: string | null;
  elapsed_ms: number;
  cancellation_confirmed: boolean;
}

export interface ProgramMatch {
  id: string;
  description: string;
  summary: string;
  match: "exact" | "semantic";
}

export interface SavedConnection {
  host: string;
  token: string;
  deviceId: string;
  pcName?: string;       // "my-pc"
}

/* ---------- Helpers ---------- */

export function parseServerMessage(raw: string): ServerMessage | null {
  try {
    const msg: unknown = JSON.parse(raw);
    return validServerMessage(msg) ? msg : null;
  } catch {
    return null;
  }
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function validServerMessage(msg: unknown): msg is ServerMessage {
  if (!record(msg)) return false;
  switch (msg.type) {
    case 'auth_ok': case 'layout_updated': case 'pong': return true;
    case 'error': return typeof msg.message === 'string';
    case 'data': return typeof msg.source === 'string' && (typeof msg.value === 'string' || finite(msg.value));
    case 'confirm_pending': return typeof msg.widget === 'string' && typeof msg.action === 'string';
    case 'status': return typeof msg.widget === 'string' && typeof msg.action === 'string' && ['running', 'done', 'error'].includes(String(msg.state)) && (msg.output === undefined || typeof msg.output === 'string');
    case 'job_update': {
      const job = msg.job;
      if (!record(job)) return false;
      const result = job.result;
      return typeof job.job_id === 'string' && typeof job.device_id === 'string' && typeof job.state === 'string'
        && (job.reason === null || typeof job.reason === 'string') && finite(job.elapsed_ms)
        && typeof job.cancellation_confirmed === 'boolean'
        && (result === null || record(result) && ['stdout', 'stderr'].every((key) => result[key] === undefined || typeof result[key] === 'string'));
    }
    case 'agent_presence': {
      const session = msg.session;
      return record(session) && typeof session.device_id === 'string' && typeof session.online === 'boolean' && (session.target === null || record(session.target) && ['Darwin', 'Windows'].includes(String(session.target.platform)) && ['arm64', 'x86_64', 'x86'].includes(String(session.target.architecture)) && typeof session.target.build_boundary_available === 'boolean' && typeof session.target.execution_boundary_available === 'boolean');
    }
    default: return false;
  }
}

/* ---------- Panel documents: validation and ordering ---------- */

/** A document from the server failed the shape check. `message` is safe to show to the user. */
export class PanelDocumentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PanelDocumentError';
  }
}

const fail = (path: string, expected: string): never => {
  throw new PanelDocumentError(`The saved panel is invalid: ${path} must be ${expected}.`);
};

const isDialType = (type: string) => (['gauge', 'bar', 'line', 'ring', 'temperature', 'number', 'log', 'stream'] as string[]).includes(type);
const optional = (value: unknown, ok: (v: unknown) => boolean) => value === undefined || ok(value);

function checkWidget(raw: unknown, path: string): void {
  if (!record(raw)) return fail(path, 'an object');
  if (typeof raw.id !== 'string' || raw.id === '') fail(`${path}.id`, 'a non-empty string');
  if (typeof raw.type !== 'string') fail(`${path}.type`, 'a string');
  if (typeof raw.label !== 'string') fail(`${path}.label`, 'a string');
  for (const key of ['x', 'y', 'w', 'h']) if (!finite(raw[key])) fail(`${path}.${key}`, 'a finite number');
  if (!optional(raw.icon, (v) => typeof v === 'string')) fail(`${path}.icon`, 'a string');
  if (!optional(raw.color, (v) => (['amber', 'cyan', 'green', 'graphite', 'coral'] as unknown[]).includes(v))) fail(`${path}.color`, 'one of the palette colors');
  for (const key of ['min', 'max', 'step', 'warn', 'critical']) if (!optional(raw[key], finite)) fail(`${path}.${key}`, 'a finite number');
  if (!optional(raw.unit, (v) => typeof v === 'string')) fail(`${path}.unit`, 'a string');
  if (!optional(raw.confirm, (v) => typeof v === 'boolean')) fail(`${path}.confirm`, 'true or false');
  if (!optional(raw.variant, (v) => v === 'switch' || v === 'push')) fail(`${path}.variant`, '"switch" or "push"');
  // Unknown widget types are allowed: the phone shows them as "Unsupported widget".
  const type = raw.type as string;
  if ((INPUT_TYPES as readonly string[]).includes(type) && typeof raw.action !== 'string') fail(`${path}.action`, 'a string');
  if (isDialType(type) && typeof raw.source !== 'string') fail(`${path}.source`, 'a string');
}

/**
 * Checks the shape of a layout document from the server: version, name, canvas or columns, and every
 * widget's fields. It does not check geometry (overlaps, bounds); `normalizeLayout` in @janus/design does.
 */
export function parsePanelDocument(raw: unknown): PanelDocument {
  if (!record(raw)) return fail('the document', 'an object');
  if (!Number.isInteger(raw.revision) || (raw.revision as number) < 0) fail('revision', 'a whole number, 0 or more');
  const layout = raw.layout;
  if (!record(layout)) return fail('layout', 'an object');
  const version = layout.schemaVersion;
  if (version !== LEGACY_SCHEMA_VERSION && version !== SCHEMA_VERSION) {
    throw new PanelDocumentError(`This panel uses layout version ${String(version)}, which this app can't show. Update Janus.`);
  }
  if (typeof layout.name !== 'string') fail('layout.name', 'a string');
  if (version === SCHEMA_VERSION) {
    if (layout.coordinateSpace !== 'logical') fail('layout.coordinateSpace', '"logical"');
    const canvas = layout.canvas;
    if (!record(canvas) || !finite(canvas.width) || !finite(canvas.height) || canvas.width <= 0 || canvas.height <= 0) {
      fail('layout.canvas', 'a width and height above 0');
    }
  } else if (!Number.isInteger(layout.columns) || (layout.columns as number) <= 0) {
    fail('layout.columns', 'a whole number above 0');
  }
  if (!Array.isArray(layout.widgets)) return fail('layout.widgets', 'a list');
  layout.widgets.forEach((widget, i) => checkWidget(widget, `layout.widgets[${i}]`));
  return raw as unknown as PanelDocument;
}

/**
 * Picks which document to show. Responses and socket updates can arrive out of order, so a document with a
 * lower revision than the one on screen never replaces it. An equal revision is the same document.
 */
export function latestDocument(current: PanelDocument | null, incoming: PanelDocument): PanelDocument {
  return current && incoming.revision < current.revision ? current : incoming;
}

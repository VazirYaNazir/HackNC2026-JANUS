export const LEGACY_SCHEMA_VERSION = 1 as const;
export const SCHEMA_VERSION = 2 as const;

/* ---------- Layout (GET /api/layout) ---------- */

export type InputType = "button" | "toggle" | "slider" | "joystick" | "dpad" | "knob" | "command" | "agent";
export type DialType = "gauge" | "bar" | "line" | "ring" | "temperature" | "number" | "log";
export type WidgetType = InputType | DialType;

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

/* ---------- Pairing (POST /api/pair) ---------- */

export interface PairRequest { code: string; name: string; }      // name: "Ana's iPhone"
export interface PairResponse { token: string; deviceId: string; }

/** What the QR code encodes. */
export interface PairQR { host: string; code: string; }          // host: "https://abc.trycloudflare.com"

/* ---------- WebSocket (/ws) ---------- */

// Phone → PC
export type ClientMessage =
  | { type: "auth"; token: string }                                   // must be first
  | { type: "trigger"; widget: string; action: string; value?: unknown };

// PC → phone
export type RunState = "running" | "done" | "error";

export type ServerMessage =
  | { type: "auth_ok" }
  | { type: "status"; widget: string; action: string; state: RunState; output?: string }
  | { type: "confirm_pending"; widget: string; action: string }      // waiting for approval on the PC
  | { type: "data"; source: string; value: number | string }
  | { type: "layout_updated" }
  | { type: "error"; message: string };

/* ---------- Saved on the phone (localStorage) ---------- */

export interface SavedConnection {
  host: string;
  token: string;
  deviceId: string;
  pcName?: string;       // "my-pc"
}

/* ---------- Helpers ---------- */

export function parseServerMessage(raw: string): ServerMessage | null {
  try {
    const msg = JSON.parse(raw);
    return msg && typeof msg.type === "string" ? (msg as ServerMessage) : null;
  } catch {
    return null;
  }
}

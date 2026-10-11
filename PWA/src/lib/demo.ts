import { gridRectToLogical, LOGICAL_CANVAS } from "@janus/design";
import {
  SCHEMA_VERSION,
  type JobView,
  type LogicalCanvasLayout,
  type SavedPanel,
  type ServerMessage,
  type Widget,
  type WidgetValue,
} from "@janus/schema";
import { PanelClient, type Dispatch, type PanelDocument, type Prepared, type Session } from "@janus/client";

/** Host of the built-in Demo panel. It never touches the network. */
export const DEMO_HOST = "demo://";
export const isDemoHost = (host: string): boolean => host === DEMO_HOST;
export const isDemoPanel = (panel: Pick<SavedPanel, "host">): boolean => isDemoHost(panel.host);

type GridWidget = { id: string; cell: [x: number, y: number, w: number, h: number] } & Record<string, unknown>;

/** Positions are given in grid cells for readability and converted to the logical canvas. */
function place(widget: GridWidget): Widget {
  const { cell, ...rest } = widget;
  const [x, y, w, h] = cell;
  return { ...rest, ...gridRectToLogical({ x, y, w, h }) } as unknown as Widget;
}

const demoWidgets: GridWidget[] = [
  { id: "backup", type: "button", label: "Backup", icon: "save", action: "demo.backup", cell: [0, 0, 2, 1] },
  { id: "spotify", type: "toggle", label: "Spotify", icon: "music", action: "demo.spotify", cell: [2, 0, 2, 1] },
  { id: "cpu", type: "gauge", label: "CPU", icon: "cpu", source: "cpu.percent", unit: "%", warn: 70, critical: 90, cell: [0, 1, 2, 2] },
  { id: "memory", type: "bar", label: "Memory", icon: "memory", source: "mem.percent", unit: "%", warn: 75, critical: 90, cell: [2, 1, 2, 1] },
  { id: "volume", type: "slider", label: "Volume", icon: "volume", action: "demo.volume", min: 0, max: 100, step: 1, cell: [2, 2, 2, 1] },
  { id: "ask", type: "agent", label: "Ask Gemini", icon: "sparkle", action: "demo.agent", cell: [0, 3, 4, 2] },
];

/** The Desk Control sample: one of each kind of control, in the builder's logical coordinates. */
export const demoLayout: LogicalCanvasLayout = {
  schemaVersion: SCHEMA_VERSION,
  coordinateSpace: "logical",
  canvas: { ...LOGICAL_CANVAS },
  name: "Desk Control",
  widgets: demoWidgets.map(place),
};

/** Starting values for the demo's stateful controls. */
export const demoValues: Record<string, WidgetValue> = { spotify: false, volume: 60 };

const DEMO_SESSION: Session = {
  device_id: "demo",
  online: true,
  target: { platform: "Windows", architecture: "x86_64", build_boundary_available: true, execution_boundary_available: true },
};

const AGENT_REPLY = "This is a canned demo answer: your next meeting is at 3pm, and your backup finished at 2:10pm.";

/** What a simulated action prints, by widget. */
function simulatedOutput(widget: Widget | undefined, value?: WidgetValue): string {
  if (!widget) return "Done (simulated)";
  if (widget.type === "agent") return AGENT_REPLY;
  if (value === undefined) return `${widget.label} finished (simulated)`;
  return `${widget.label} set to ${typeof value === "object" ? JSON.stringify(value) : String(value)} (simulated)`;
}

const wander = (value: number, lo: number, hi: number) =>
  Math.round(Math.min(hi, Math.max(lo, value + (Math.random() - 0.5) * 12)) * 10) / 10;

/**
 * A PanelClient that answers from fixtures: the demo layout, fake dial readings, and simulated job replies.
 * `request()` always throws, so nothing in here can reach the network.
 */
export class DemoClient extends PanelClient {
  private jobs: JobView[] = [];
  private receive: ((message: ServerMessage) => void) | null = null;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private cancelled = new Set<string>();

  constructor() {
    // Never fetched from: only here so the base class has a valid origin.
    super({ host: "https://demo.invalid", token: "demo" });
  }

  override request<T>(): Promise<T> {
    return Promise.reject(new Error("The demo panel makes no network calls."));
  }

  override layout(): Promise<PanelDocument> {
    return Promise.resolve({ layout: demoLayout, revision: 1 });
  }
  override history(): Promise<{ jobs: JobView[] }> {
    return Promise.resolve({ jobs: this.jobs });
  }
  override session(): Promise<Session> {
    return Promise.resolve(DEMO_SESSION);
  }

  override prepare(payload: Dispatch): Promise<Prepared> {
    const widget = demoLayout.widgets.find((w) => w.id === payload.widget_id);
    return Promise.resolve({
      request: {
        description: `Simulated: ${widget?.label ?? payload.widget_id}`,
        operation: "demo",
        platform: "Windows",
        architecture: "x86_64",
        capabilities: [],
        dependencies: [],
        output_protocol: "text",
      },
      request_sha256: "demo",
      checked: true,
      matches: [],
    });
  }

  override dispatch(payload: Dispatch): Promise<JobView> {
    const widget = demoLayout.widgets.find((w) => w.id === payload.widget_id);
    const job = (state: string, elapsed: number, stdout?: string): JobView => ({
      job_id: payload.job_id,
      device_id: "demo",
      state,
      reason: null,
      result: stdout === undefined ? null : { stdout, status: "ok" },
      widget_id: payload.widget_id,
      action_id: null,
      elapsed_ms: elapsed,
      cancellation_confirmed: false,
    });
    const later = (ms: number, view: JobView) => {
      const timer = setTimeout(() => {
        this.timers.delete(timer);
        if (this.cancelled.has(view.job_id)) return;
        this.jobs = [view, ...this.jobs.filter((j) => j.job_id !== view.job_id)];
        this.receive?.({ type: "job_update", job: view });
      }, ms);
      this.timers.add(timer);
    };
    later(500, job("EXECUTING", 500));
    later(1400, job("COMPLETED", 1400, simulatedOutput(widget, payload.value)));
    return Promise.resolve(job("QUEUED", 0));
  }

  override cancel(jobId: string): Promise<JobView> {
    this.cancelled.add(jobId);
    return Promise.resolve({
      job_id: jobId, device_id: "demo", state: "CANCELLED", reason: "Cancelled", result: null,
      elapsed_ms: 0, cancellation_confirmed: true,
    });
  }

  override subscribe(receive: (message: ServerMessage) => void, status: (value: string) => void): () => void {
    this.receive = receive;
    status("connected");
    let cpu = 42;
    let mem = 68;
    const open = setTimeout(() => receive({ type: "auth_ok" }), 0);
    const tick = setInterval(() => {
      cpu = wander(cpu, 8, 96);
      mem = wander(mem, 40, 92);
      receive({ type: "data", source: "cpu.percent", value: cpu });
      receive({ type: "data", source: "mem.percent", value: mem });
    }, 1000);
    return () => {
      clearTimeout(open);
      clearInterval(tick);
      this.timers.forEach(clearTimeout);
      this.timers.clear();
      this.receive = null;
    };
  }
}

/** The right client for a saved panel: fixtures for the Demo panel, the real backend otherwise. */
export const createClient = (panel: SavedPanel): PanelClient => (isDemoPanel(panel) ? new DemoClient() : new PanelClient(panel));

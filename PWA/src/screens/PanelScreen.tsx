import { useEffect, useMemo, useRef, useState } from "react";
import { GRID, Icon } from "@janus/design";
import { isNativeAction, type SavedPanel, type Widget, type WidgetValue } from "@janus/schema";
import { PanelGrid, isInputWidget } from "@janus/widgets";
import { RequestDialog, activeJob, jobText, useMetricHistory, useNativeControls, usePanel, widgetStates, widgetErrors } from "@janus/client";
import "@janus/client/styles.css";
import { createClient, demoValues, isDemoPanel } from "../lib/demo";
import { loadCachedDocument, saveCachedDocument } from "../lib/layoutCache";
import "./PanelScreen.css";

export default function PanelScreen({ panel, onBack, onRepair }: { panel: SavedPanel; onBack: () => void; onRepair: () => void }) {
  const client = useMemo(() => createClient(panel), [panel]);
  const demo = isDemoPanel(panel);
  const live = usePanel(client);
  const [values, setValues] = useState<Record<string, WidgetValue>>(() => (demo ? demoValues : {}));
  const [pending, setPending] = useState<{ widget: Widget; value?: WidgetValue } | null>(null);
  const [error, setError] = useState("");
  const promptAcceptance = useRef<((accepted: boolean) => void) | null>(null);
  const closeRequest = () => { promptAcceptance.current?.(false); promptAcceptance.current = null; setPending(null); };
  useEffect(() => () => { promptAcceptance.current?.(false); promptAcceptance.current = null; }, [client]);
  const controls = useNativeControls(client, setError);

  // The last valid layout is kept on the phone, so the panel can be shown (view-only) while the PC is unreachable.
  const [cached] = useState(() => (demo ? null : loadCachedDocument(panel.id)));
  const shown = live.document ?? cached;
  // Last-hour sparkline data for gauges, when the PC's backend has Tiger Data. Empty (and invisible) otherwise.
  const history = useMetricHistory(demo ? null : client, (shown?.layout.widgets ?? []).flatMap((widget) => (isInputWidget(widget) ? [] : [widget.source])));
  useEffect(() => {
    if (live.document && !demo) saveCachedDocument(panel.id, live.document);
  }, [live.document, panel.id, demo]);

  // Actions need a live connection to a PC that is online. Without one the controls are view-only.
  const unauthorized = live.status === "unauthorized";
  const online = live.status === "connected" && live.session?.online !== false;
  const blockedMessage = unauthorized ? "Pair again to use this panel." : "Your PC is offline.";
  const [notice, setNotice] = useState("");
  const noticeTimer = useRef<number>(undefined);
  const showNotice = (text: string) => {
    setNotice(text);
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(""), 2500);
  };
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);
  const states = widgetStates(live.jobs);
  const handleTrigger = (widget: Widget, value?: WidgetValue) => {
    if (!isInputWidget(widget)) return;
    if (!online) return showNotice(blockedMessage);
    if (!demo && !widget.action) return showNotice('Configure this control in Studio, then Save & Push.');
    if (value !== undefined) setValues((current) => ({ ...current, [widget.id]: value }));
    if (!demo && isNativeAction(widget.action)) {
      if (!widget.confirm || window.confirm(`Run ${widget.label} on ${panel.pcName}?`)) controls.send(widget, value);
      return;
    }
    if (!pending) {
      setPending({ widget, value });
      if (widget.type === 'agent' || widget.type === 'command') return new Promise<boolean>(resolve => { promptAcceptance.current = resolve; });
    }
  };

  return <main className="panel" style={{ padding: `${GRID.padding}px` }}>
    <header className="panel__header">
      <button type="button" className="panel__back" aria-label="Back to panels" onClick={onBack}><Icon name="back" size={24} /></button>
      <h1 className="panel__title">{panel.name}</h1>
    </header>
    {demo && <p className="panel__demo">Demo panel · Actions are simulated</p>}
    {unauthorized
      ? <section className="panel__auth" role="alert">
          <p>This phone is no longer paired with your PC.</p>
          <button type="button" className="btn btn--amber" onClick={onRepair}>Pair again</button>
        </section>
      : <p role="status">{live.session?.online ? `Laptop online · ${live.status}` : `Laptop offline · ${live.status}`}</p>}
    {(live.error || error) && <p className="error" role="alert">{error || live.error}</p>}
    {!live.document && cached && <p className="panel__cached">Showing your last saved layout.</p>}
    {shown ? <PanelGrid fit="content" layout={shown.layout} values={values} data={live.readings} history={history} states={states} errors={widgetErrors(live.jobs)} onTrigger={handleTrigger} readOnly={!online} onBlocked={() => showNotice(blockedMessage)} /> : unauthorized ? null : live.error ? <p>This panel can't be shown right now.</p> : live.status === "disconnected" ? <p>Can't reach your PC. This panel will appear when it connects.</p> : <p>Loading your saved panel…</p>}
    {shown?.layout.widgets.length === 0 && <p>Add controls in the desktop Studio, then choose Save &amp; Push.</p>}
    {live.jobs.length > 0 && <section className="panel__jobs" aria-label="Recent requests"><h2>Recent requests</h2>{live.jobs.slice(0, 10).map((job) => <article key={job.job_id}>
      <strong>{job.state}</strong><small>{job.job_id}</small><p>{jobText(job)}</p>
      {activeJob(job) && <button type="button" className="btn" onClick={() => void client.cancel(job.job_id).then(live.recordJob).catch((reason) => setError(String(reason.message)))}>Cancel request</button>}
    </article>)}</section>}
    {notice && <p className="toast" role="status">{notice}</p>}
    {pending && <RequestDialog client={client} session={live.session} {...pending} onClose={closeRequest} onSubmitted={(job) => { promptAcceptance.current?.(true); promptAcceptance.current = null; live.recordJob(job); }} />}
  </main>;
}

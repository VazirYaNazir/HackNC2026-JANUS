import { useMemo, useState } from "react";
import { GRID, Icon } from "@janus/design";
import type { SavedPanel, Widget, WidgetValue } from "@janus/schema";
import { PanelGrid, isInputWidget } from "@janus/widgets";
import { PanelClient, RequestDialog, activeJob, jobText, usePanel } from "@janus/client";
import "@janus/client/styles.css";
import "./PanelScreen.css";

export default function PanelScreen({ panel, onBack }: { panel: SavedPanel; onBack: () => void }) {
  const client = useMemo(() => new PanelClient(panel), [panel]);
  const live = usePanel(client);
  const [values, setValues] = useState<Record<string, WidgetValue>>({});
  const [pending, setPending] = useState<{ widget: Widget; value?: WidgetValue } | null>(null);
  const [error, setError] = useState("");
  const states: Record<string, string> = {};
  const data: Record<string, number | string> = {};
  for (const job of live.jobs) {
    if (job.widget_id && states[job.widget_id] === undefined) states[job.widget_id] = activeJob(job) ? "running" : job.state === "COMPLETED" ? jobText(job) : "error";
    try {
      const output: unknown = JSON.parse(job.result?.stdout ?? "null");
      if (typeof output === "object" && output !== null && !Array.isArray(output)) {
        for (const [key, value] of Object.entries(output)) {
          if (key !== "version" && data[key] === undefined && (typeof value === "string" || typeof value === "number" && Number.isFinite(value))) data[key] = value;
        }
      }
    } catch { /* Plain text output is shown in job history. */ }
  }
  const handleTrigger = (widget: Widget, value?: WidgetValue) => {
    if (!isInputWidget(widget)) return;
    if (value !== undefined) setValues((current) => ({ ...current, [widget.id]: value }));
    if (!pending) setPending({ widget, value });
  };

  return <main className="panel" style={{ padding: `${GRID.padding}px` }}>
    <header className="panel__header">
      <button type="button" className="panel__back" aria-label="Back to panels" onClick={onBack}><Icon name="back" size={24} /></button>
      <h1 className="panel__title">{panel.name}</h1>
    </header>
    <p role="status">{live.status === "unauthorized" ? "Pair again: this connection is no longer authorized." : live.session?.online ? `Laptop online · ${live.status}` : `Laptop offline · ${live.status}`}</p>
    {(live.error || error) && <p className="error" role="alert">{error || live.error}</p>}
    {live.document ? <PanelGrid layout={live.document.layout} values={values} data={data} states={states} onTrigger={handleTrigger} /> : <p>Loading your saved panel…</p>}
    {live.document?.layout.widgets.length === 0 && <p>Add controls in the desktop Studio, then choose Save &amp; Push.</p>}
    {live.jobs.length > 0 && <section className="panel__jobs" aria-label="Recent requests"><h2>Recent requests</h2>{live.jobs.slice(0, 10).map((job) => <article key={job.job_id}>
      <strong>{job.state}</strong><small>{job.job_id}</small><p>{jobText(job)}</p>
      {activeJob(job) && <button type="button" className="btn" onClick={() => void client.cancel(job.job_id).then(live.recordJob).catch((reason) => setError(String(reason.message)))}>Cancel request</button>}
    </article>)}</section>}
    {pending && <RequestDialog client={client} {...pending} onClose={() => setPending(null)} onSubmitted={live.recordJob} />}
  </main>;
}

import { useEffect, useMemo, useRef } from 'react';
import { isNativeAction, type InputWidget, type WidgetValue } from '@janus/schema';
import { PanelClient } from './api';

/** Serialize each bound action, coalescing movement while an acknowledgement is in flight. */
export class NativeControls {
  private pending = new Map<string, { widget: InputWidget; value?: WidgetValue }[]>();
  private active = new Map<string, Promise<void>>();
  private stopped = false;
  private client: PanelClient;
  private onError: (message: string) => void;
  constructor(client: PanelClient, onError: (message: string) => void) { this.client = client; this.onError = onError; }
  send(widget: InputWidget, value?: WidgetValue) {
    if (this.stopped || !isNativeAction(widget.action)) return;
    const queue = this.pending.get(widget.action) ?? [];
    if (['slider', 'knob', 'joystick'].includes(widget.type)) queue.splice(0);
    if (queue.length >= 32) { this.onError('Too many pending native controls.'); return; }
    queue.push({ widget, value });
    this.pending.set(widget.action, queue);
    if (this.active.has(widget.action)) return;
    const drain = async () => {
      while (!this.stopped && queue.length) {
        const next = queue.shift()!;
        try { await this.client.native({ widget_id: next.widget.id, job_id: crypto.randomUUID(), value: next.value }); }
        catch (reason) { if (!this.stopped) this.onError(reason instanceof Error ? reason.message : 'Native control failed.'); }
      }
      this.active.delete(widget.action);
    };
    this.active.set(widget.action, drain());
  }
  async flush() { await Promise.all(this.active.values()); }
  close() { this.stopped = true; this.pending.clear(); }
}

export function useNativeControls(client: PanelClient | null, onError: (message: string) => void) {
  const error = useRef(onError);
  useEffect(() => { error.current = onError; });
  const current = useRef<NativeControls | null>(null);
  useEffect(() => {
    const controls = client ? new NativeControls(client, (message) => error.current(message)) : null;
    current.current = controls;
    return () => { controls?.close(); current.current = null; };
  }, [client]);
  return useMemo(() => ({ send: (widget: InputWidget, value?: WidgetValue) => current.current?.send(widget, value) }), []);
}

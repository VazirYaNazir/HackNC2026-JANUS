import { useCallback, useEffect, useRef, useState } from 'react';
import type { JobView } from '@janus/schema';
import { PanelClient, type PanelDocument, type Session } from './api';
import { mergeHistory, recordJob as addJob } from './jobs';

interface PanelState {
  client: PanelClient | null;
  document: PanelDocument | null;
  session: Session | null;
  jobs: JobView[];
  /** Latest value per dial source, from `data` messages. */
  readings: Record<string, number | string>;
  error: string;
}
const empty = (client: PanelClient | null): PanelState => ({ client, document: null, session: null, jobs: [], readings: {}, error: '' });

export function usePanel(client: PanelClient | null) {
  const [snapshot, setSnapshot] = useState<PanelState>(() => empty(null));
  const [status, setStatus] = useState('disconnected');
  const activeClient = useRef<PanelClient | null>(null);
  const recordJob = useCallback((job: JobView) => {
    if (activeClient.current !== client) return;
    setSnapshot((state) => {
      const scoped = state.client === client ? state : empty(client);
      return { ...scoped, jobs: addJob(scoped.jobs, job) };
    });
  }, [client]);
  const setDocument = useCallback((document: PanelDocument) => {
    if (activeClient.current !== client) return;
    setSnapshot((state) => ({ ...(state.client === client ? state : empty(client)), document }));
  }, [client]);

  useEffect(() => {
    activeClient.current = client;
    if (!client) return;
    const abort = new AbortController();
    let current = true;
    const update = (change: (state: PanelState) => PanelState) => {
      if (current) setSnapshot((state) => change(state.client === client ? state : empty(client)));
    };
    const fail = (reason: unknown) => update((state) => ({ ...state, error: reason instanceof Error ? reason.message : 'Could not load the panel.' }));
    const refreshLayout = () => client.layout(abort.signal).then((document) => update((state) => ({ ...state, document }))).catch(fail);
    const refresh = async () => {
      try {
        const [document, history, session] = await Promise.all([client.layout(abort.signal), client.history(abort.signal), client.session(abort.signal)]);
        update((state) => ({ ...state, document, jobs: mergeHistory(state.jobs, history.jobs), session, error: '' }));
      } catch (reason) { fail(reason); }
    };
    const close = client.subscribe((message) => {
      if (!current) return;
      if (message.type === 'auth_ok') void refresh();
      else if (message.type === 'layout_updated') void refreshLayout();
      else if (message.type === 'data') update((state) => ({ ...state, readings: { ...state.readings, [message.source]: message.value } }));
      else if (message.type === 'job_update') recordJob(message.job);
      else if (message.type === 'agent_presence') update((state) => ({ ...state, session: message.session }));
      else if (message.type === 'error') update((state) => ({ ...state, error: message.message }));
    }, (value) => { if (current) setStatus(value); });
    return () => { current = false; activeClient.current = null; abort.abort(); close(); };
  }, [client, recordJob]);
  // A new connection must never display another laptop's previous state.
  const scoped = snapshot.client === client ? snapshot : empty(client);
  return { ...scoped, status: client ? status : 'disconnected', setDocument, recordJob };
}

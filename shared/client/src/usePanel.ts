import { useCallback, useEffect, useRef, useState } from 'react';
import { latestDocument, type JobView } from '@janus/schema';
import { ApiError, PanelClient, type PanelDocument, type Session } from './api';
import { mergeHistory, recordJob as addJob } from './jobs';

interface PanelState {
  client: PanelClient | null;
  document: PanelDocument | null;
  session: Session | null;
  jobs: JobView[];
  /** Latest value per dial source, from `data` messages. */
  readings: Record<string, number | string>;
  /** Why the layout could not be loaded or shown; cleared as soon as a valid layout arrives. */
  layoutError: string;
  error: string;
}
const empty = (client: PanelClient | null): PanelState => ({ client, document: null, session: null, jobs: [], readings: {}, layoutError: '', error: '' });

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
    setSnapshot((state) => {
      const scoped = state.client === client ? state : empty(client);
      return { ...scoped, document: latestDocument(scoped.document, document) };
    });
  }, [client]);

  useEffect(() => {
    activeClient.current = client;
    if (!client) return;
    const abort = new AbortController();
    let current = true;
    const update = (change: (state: PanelState) => PanelState) => {
      if (current) setSnapshot((state) => change(state.client === client ? state : empty(client)));
    };
    const messageOf = (reason: unknown) => {
      // A 401 means the PC no longer accepts this phone's token (revoked or reset): the panel must be paired again.
      if (reason instanceof ApiError && reason.status === 401 && current) setStatus('unauthorized');
      return reason instanceof Error ? reason.message : 'Could not load the panel.';
    };
    const refreshLayout = () => client.layout(abort.signal)
      .then((document) => update((state) => ({ ...state, document: latestDocument(state.document, document), layoutError: '' })))
      .catch((reason) => update((state) => ({ ...state, layoutError: messageOf(reason) })));
    const refresh = async () => {
      // Each part applies on its own: an invalid layout must not hide the job history or the laptop's status.
      const [layout, history, session] = await Promise.allSettled([client.layout(abort.signal), client.history(abort.signal), client.session(abort.signal)]);
      const failure = [history, session].find((result): result is PromiseRejectedResult => result.status === 'rejected');
      update((state) => ({
        ...state,
        document: layout.status === 'fulfilled' ? latestDocument(state.document, layout.value) : state.document,
        jobs: history.status === 'fulfilled' ? mergeHistory(state.jobs, history.value.jobs) : state.jobs,
        session: session.status === 'fulfilled' ? session.value : state.session,
        layoutError: layout.status === 'rejected' ? messageOf(layout.reason) : '',
        error: failure ? messageOf(failure.reason) : '',
      }));
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
  return { ...scoped, error: scoped.layoutError || scoped.error, status: client ? status : 'disconnected', setDocument, recordJob };
}

import { useCallback, useEffect, useRef, useState } from 'react'
import '@janus/design/tokens.css'
import { normalizeLayout } from '@janus/design'
import { isNativeAction, type GeneratedDraft, type GenerationRequest, type LogicalCanvasLayout, type Widget, type WidgetType, type WidgetValue } from '@janus/schema'
import { isInputWidget } from '@janus/widgets'
import { PanelClient, RequestDialog, SourceReview, activeJob, jobText, useMetricHistory, useNativeControls, usePanel, widgetStates, type Connection } from '@janus/client'
import '@janus/client/styles.css'
import { TopBar } from './components/TopBar'
import { WidgetPalette } from './components/WidgetPalette'
import { EditorCanvas } from './components/EditorCanvas'
import { Inspector } from './components/Inspector'
import { BottomBar } from './components/BottomBar'
import { AgentHelp } from './components/AgentHelp'
import { ActivityPanel } from './components/ActivityPanel'
import { GenerationStatus } from './components/GenerationStatus'
import { useActivity } from './useActivity'
import { useMessagingStatus } from './useMessagingStatus'
import { ConnectionDialog } from './components/ConnectionDialog'
import { PairingDialog } from './components/PairingDialog'
import { ProjectDialog } from './components/ProjectDialog'
import { useEditorState } from './editor/useEditorState'
import { startupConnection } from './editor/startup'
import './styles/glass.css'
import './styles/studio.css'
import './styles/connection.css'

function App() {
  const editor = useEditorState()
  const loadLayout = editor.loadLayout
  const [pendingType, setPendingType] = useState<WidgetType | null>(null)
  const [client, setClient] = useState<PanelClient | null>(null)
  const live = usePanel(client)
  const messaging = useMessagingStatus(client)
  const activity = useActivity(client)
  const history = useMetricHistory(client, editor.layout.widgets.flatMap((widget) => (isInputWidget(widget) ? [] : [widget.source])))
  const [revision, setRevision] = useState(0)
  const [sheet, setSheet] = useState<'connect' | 'pair' | 'history' | 'project' | null>(null)
  const [pending, setPending] = useState<{ widget: Widget; value?: WidgetValue } | null>(null)
  const [draft, setDraft] = useState<GeneratedDraft | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [dismissed, setDismissed] = useState('')
  const controls = useNativeControls(client, setNotice)
  const startup = useRef<Promise<Connection | null> | null>(null)
  const manualConnection = useRef(false)
  const generation = useRef<AbortController | null>(null)
  const message = notice || live.error || (live.document && live.document.revision !== revision ? 'The saved panel changed in another window.' : '')
  const canExecute = live.session?.online === true && live.session.target?.build_boundary_available === true && live.session.target.execution_boundary_available === true
  const selectedControl = editor.selectedWidget && isInputWidget(editor.selectedWidget) ? editor.selectedWidget : null
  const sourceOnly = !selectedControl || !canExecute
  const generationEnabled = client !== null && live.status === 'connected' && live.session?.generation_available !== false && !busy
  const generationDisabledReason = !client ? 'Connect your backend to generate C source.' : live.status !== 'connected' ? 'Reconnect your backend to generate C source.' : live.session?.generation_available === false ? 'Generation needs a Gemini API key in the backend environment. Add the key and restart JANUS.' : 'Wait for the current request to finish.'

  const adoptClient = useCallback((next: PanelClient) => {
    generation.current?.abort(); generation.current = null
    setDraft(null); setPending(null); setBusy(false); setClient(next); setNotice(''); setDismissed('')
  }, [])

  useEffect(() => () => generation.current?.abort(), [])

  useEffect(() => {
    let active = true
    startup.current ??= startupConnection(window.location, window.history)
    void startup.current.then(async connection => {
      if (!connection || !active || manualConnection.current) return
      const next = new PanelClient(connection)
      await next.request('/api/v1/studio/session')
      const document = await next.layout()
      if (!active || manualConnection.current) return
      loadLayout(normalizeLayout(document.layout)); setRevision(document.revision); adoptClient(next)
    }).catch(reason => { if (active && !manualConnection.current) setNotice(reason instanceof Error ? reason.message : 'Startup connection failed. Connect manually.') })
    return () => { active = false }
  }, [adoptClient, loadLayout])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const editingText = target?.matches('input, textarea, select, [contenteditable="true"]')
      if (editingText || editor.mode !== 'edit' || sheet || pending || draft || (busy && !sourceOnly)) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) editor.redo()
        else editor.undo()
      } else if ((event.key === 'Delete' || event.key === 'Backspace') && editor.selectedId) {
        event.preventDefault(); editor.removeWidget(editor.selectedId)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [editor, pending, sheet, draft, busy, sourceOnly])

  const save = async (layout: LogicalCanvasLayout = editor.layout) => {
    if (!client) return
    setBusy(true); setNotice('')
    try {
      const document = await client.save(layout, revision)
      setRevision(document.revision); live.setDocument(document); setNotice('Panel saved and pushed to paired phones.')
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : 'Save failed.'); throw reason }
    finally { setBusy(false) }
  }

  const prepare = async (request: GenerationRequest) => {
    const selected = editor.selectedWidget
    if (!client || !generationEnabled) return
    setBusy(true)
    let abort: AbortController | undefined
    try {
      if (!selected || !isInputWidget(selected) || !canExecute) {
        abort = new AbortController()
        generation.current = abort
        setNotice('')
        const generated = await client.generate(request, abort.signal)
        if (!abort.signal.aborted) setDraft(generated)
        return
      }
      // An unpublished definition may remain if saving fails; existing controls
      // must keep their previous definition and working binding in that case.
      const widget = { ...selected, action: `ai_${crypto.randomUUID()}` }
      const layout = { ...editor.layout, widgets: editor.layout.widgets.map((item) => item.id === widget.id ? widget : item) }
      await client.configure(widget.action, request)
      await save(layout)
      editor.changeWidget(widget.id, () => widget)
      const value = widget.type === 'agent' || widget.type === 'command' ? request.description : widget.type === 'button' ? undefined : editor.values[widget.id] ?? (widget.type === 'toggle' ? false : widget.type === 'joystick' ? { x: 0, y: 0 } : widget.type === 'dpad' ? 'center' : widget.min ?? 0)
      setPending({ widget, value })
    } catch (reason) { if (!abort?.signal.aborted) setNotice(reason instanceof Error ? reason.message : 'Could not generate this program.') }
    finally { if (!abort?.signal.aborted) setBusy(false) }
  }

  const trigger = (widget: Widget, value?: WidgetValue) => {
    editor.triggerWidget(widget, value)
    if (!client) { setSheet('connect'); return }
    if (live.status !== 'connected' || live.session?.online === false) { setNotice('Connect your laptop agent before using this control.'); return }
    if (isInputWidget(widget) && !widget.action) { setNotice('Select this control in Edit mode and use the AI assistant or choose a built-in action in Inspector → Logic.'); return }
    if (isInputWidget(widget) && isNativeAction(widget.action)) {
      if (!widget.confirm || window.confirm(`Run ${widget.label} on your laptop?`)) controls.send(widget, value)
      return
    }
    if (!pending && isInputWidget(widget)) setPending({ widget, value })
  }
  const reload = () => {
    if (live.document) { editor.loadLayout(normalizeLayout(live.document.layout)); setRevision(live.document.revision); setNotice('Loaded the saved panel.') }
  }

  return <div className="studio-shell">
    <TopBar name={editor.layout.name} onName={editor.renameLayout} onProject={() => setSheet('project')} connected={client !== null && live.status === 'connected'} status={client ? `${live.session?.online ? 'Laptop online' : 'Laptop offline'} · ${live.status}` : 'Connect backend'} busy={busy} onConnect={() => setSheet('connect')} onPair={() => setSheet('pair')} onSave={() => void save().catch(() => undefined)} onHistory={() => setSheet('history')} />
    <div className="studio-body" inert={busy && !sourceOnly}>
      <WidgetPalette onDragStart={setPendingType} onDragEnd={() => setPendingType(null)} disabled={editor.mode === 'preview'} />
      <EditorCanvas layout={editor.layout} selectedId={editor.selectedId} pendingType={pendingType} snapToGrid={editor.snapToGrid} mode={editor.mode} values={editor.values} data={live.readings} history={history} states={widgetStates(live.jobs)} canUndo={editor.canUndo} canRedo={editor.canRedo} onSelect={editor.selectWidget} onInsert={editor.addWidget} onGeometryChange={editor.changeGeometry} onTrigger={trigger} onToggleSnap={() => editor.setSnapToGrid((current) => !current)} onToggleMode={() => editor.setMode((current) => current === 'edit' ? 'preview' : 'edit')} onUndo={editor.undo} onRedo={editor.redo} />
      <Inspector widget={editor.selectedWidget} mode={editor.mode} error={editor.error} onGeometryChange={editor.changeGeometry} onWidgetChange={editor.changeWidget} onDelete={editor.removeWidget} />
    </div>
    <ActivityPanel feed={activity} />
    <GenerationStatus session={client ? live.session : null} messaging={messaging} />
    <BottomBar key={editor.selectedId ?? 'none'} enabled={generationEnabled} disabledReason={generationDisabledReason} sourceOnly={sourceOnly} hint={sourceOnly ? 'Generate C source to review and download. Select a control with an available execution adapter to configure it.' : `Configure ${selectedControl?.label}, then review the request before execution.`} target={live.session?.target ?? null} onGenerate={prepare} />
    {message && message !== dismissed && <div className="studio-notice" role="status">{message}<button type="button" onClick={reload} disabled={!live.document}>Load saved panel</button><button type="button" onClick={() => { setDismissed(message); setNotice('') }}>Dismiss</button></div>}
    {client && live.status === 'connected' && live.session?.online === false && !message && <AgentHelp />}
    {sheet === 'project' && <ProjectDialog layout={editor.layout} onReplace={editor.replaceLayout} onClose={() => setSheet(null)} />}
    {sheet === 'connect' && <ConnectionDialog onClose={() => setSheet(null)} onConnect={async (next) => {
      manualConnection.current = true
      const document = await next.layout()
      editor.loadLayout(normalizeLayout(document.layout)); setRevision(document.revision); adoptClient(next)
    }} />}
    {sheet === 'pair' && client && <PairingDialog client={client} onClose={() => setSheet(null)} />}
    {sheet === 'history' && client && <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="history-heading"><h2 id="history-heading">Recent requests</h2>{live.jobs.length ? live.jobs.map((job) => <article key={job.job_id}><strong>{job.state}</strong><p>{job.job_id}</p><pre>{jobText(job)}</pre>{activeJob(job) && <button type="button" onClick={() => void client.cancel(job.job_id).then(live.recordJob).catch((reason) => setNotice(String(reason.message)))}>Cancel request</button>}</article>) : <p>No requests yet.</p>}<button type="button" onClick={() => setSheet(null)}>Close</button></section></div>}
    {pending && client && <RequestDialog client={client} session={live.session} {...pending} onClose={() => setPending(null)} onSubmitted={(job) => { live.recordJob(job); setSheet('history') }} />}
    {draft && <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="source-heading"><h2 id="source-heading">Generation complete</h2><SourceReview draft={draft} /><p>{canExecute ? 'Select a control to configure a request. Execution still requires fresh local validation and approval.' : live.session?.online ? 'Execution is unavailable on this laptop until a build and execution isolation adapter is installed.' : 'Connect a laptop with a build and execution isolation adapter to request execution.'}</p><div className="janus-actions"><button type="button" onClick={() => setDraft(null)}>Close</button></div></section></div>}
  </div>
}

export default App

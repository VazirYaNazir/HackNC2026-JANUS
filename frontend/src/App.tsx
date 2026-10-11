import { useEffect, useState } from 'react'
import '@janus/design/tokens.css'
import { normalizeLayout } from '@janus/design'
import type { GenerationRequest, LogicalCanvasLayout, Widget, WidgetType, WidgetValue } from '@janus/schema'
import { isInputWidget } from '@janus/widgets'
import { PanelClient, RequestDialog, activeJob, jobText, usePanel } from '@janus/client'
import '@janus/client/styles.css'
import { TopBar } from './components/TopBar'
import { WidgetPalette } from './components/WidgetPalette'
import { EditorCanvas } from './components/EditorCanvas'
import { Inspector } from './components/Inspector'
import { BottomBar } from './components/BottomBar'
import { ConnectionDialog } from './components/ConnectionDialog'
import { PairingDialog } from './components/PairingDialog'
import { useEditorState } from './editor/useEditorState'
import './styles/glass.css'
import './styles/studio.css'
import './styles/connection.css'

function App() {
  const editor = useEditorState()
  const [pendingType, setPendingType] = useState<WidgetType | null>(null)
  const [client, setClient] = useState<PanelClient | null>(null)
  const live = usePanel(client)
  const [revision, setRevision] = useState(0)
  const [sheet, setSheet] = useState<'connect' | 'pair' | 'history' | null>(null)
  const [pending, setPending] = useState<{ widget: Widget; value?: WidgetValue } | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const editingText = target?.matches('input, textarea, select, [contenteditable="true"]')
      if (editingText || editor.mode !== 'edit' || sheet || pending) return
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
  }, [editor, pending, sheet])

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
    if (!client || !selected || !isInputWidget(selected)) return
    try {
      const widget = { ...selected, action: selected.action || selected.id }
      const layout = { ...editor.layout, widgets: editor.layout.widgets.map((item) => item.id === widget.id ? widget : item) }
      await save(layout)
      await client.configure(widget.action, request)
      editor.changeWidget(widget.id, () => widget)
      const value = widget.type === 'agent' || widget.type === 'command' ? request.description : widget.type === 'button' ? undefined : editor.values[widget.id] ?? (widget.type === 'toggle' ? false : widget.type === 'joystick' ? { x: 0, y: 0 } : widget.type === 'dpad' ? 'center' : widget.min ?? 0)
      setPending({ widget, value })
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : 'Could not prepare this control.') }
  }

  const trigger = (widget: Widget, value?: WidgetValue) => {
    editor.triggerWidget(widget, value)
    if (!client) { setSheet('connect'); return }
    if (!pending && isInputWidget(widget)) setPending({ widget, value })
  }
  const reload = () => {
    if (live.document) { editor.loadLayout(normalizeLayout(live.document.layout)); setRevision(live.document.revision); setNotice('Loaded the saved panel.') }
  }

  return <div className="studio-shell">
    <TopBar name={editor.layout.name} connected={client !== null && live.status === 'connected'} status={client ? `${live.session?.online ? 'Laptop online' : 'Laptop offline'} · ${live.status}` : 'Connect backend'} busy={busy} onConnect={() => setSheet('connect')} onPair={() => setSheet('pair')} onSave={() => void save().catch(() => undefined)} onHistory={() => setSheet('history')} />
    <div className="studio-body">
      <WidgetPalette onDragStart={setPendingType} onDragEnd={() => setPendingType(null)} disabled={editor.mode === 'preview'} />
      <EditorCanvas layout={editor.layout} selectedId={editor.selectedId} pendingType={pendingType} snapToGrid={editor.snapToGrid} mode={editor.mode} values={editor.values} canUndo={editor.canUndo} canRedo={editor.canRedo} onSelect={editor.selectWidget} onInsert={editor.addWidget} onGeometryChange={editor.changeGeometry} onTrigger={trigger} onToggleSnap={() => editor.setSnapToGrid((current) => !current)} onToggleMode={() => editor.setMode((current) => current === 'edit' ? 'preview' : 'edit')} onUndo={editor.undo} onRedo={editor.redo} />
      <Inspector widget={editor.selectedWidget} mode={editor.mode} error={editor.error} onGeometryChange={editor.changeGeometry} onWidgetChange={editor.changeWidget} onDelete={editor.removeWidget} />
    </div>
    <BottomBar key={editor.selectedId ?? 'none'} enabled={client !== null && !!editor.selectedWidget && isInputWidget(editor.selectedWidget) && !busy} target={live.session?.target ?? null} onGenerate={prepare} />
    {(notice || live.error || live.document && live.document.revision !== revision) && <div className="studio-notice" role="status">{notice || live.error || 'The saved panel changed in another window.'}<button type="button" onClick={reload} disabled={!live.document}>Load saved panel</button><button type="button" onClick={() => setNotice('')}>Dismiss</button></div>}
    {sheet === 'connect' && <ConnectionDialog onClose={() => setSheet(null)} onConnect={async (next) => {
      const document = await next.layout()
      editor.loadLayout(normalizeLayout(document.layout)); setRevision(document.revision); setClient(next); setPending(null); setNotice('')
    }} />}
    {sheet === 'pair' && client && <PairingDialog client={client} onClose={() => setSheet(null)} />}
    {sheet === 'history' && client && <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="history-heading"><h2 id="history-heading">Recent requests</h2>{live.jobs.length ? live.jobs.map((job) => <article key={job.job_id}><strong>{job.state}</strong><p>{job.job_id}</p><pre>{jobText(job)}</pre>{activeJob(job) && <button type="button" onClick={() => void client.cancel(job.job_id).then(live.recordJob).catch((reason) => setNotice(String(reason.message)))}>Cancel request</button>}</article>) : <p>No requests yet.</p>}<button type="button" onClick={() => setSheet(null)}>Close</button></section></div>}
    {pending && client && <RequestDialog client={client} {...pending} onClose={() => setPending(null)} onSubmitted={(job) => { live.recordJob(job); setSheet('history') }} />}
  </div>
}

export default App

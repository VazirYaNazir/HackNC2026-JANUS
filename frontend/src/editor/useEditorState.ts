import { useCallback, useMemo, useState } from 'react'
import type { Rect } from '@janus/design'
import type { LogicalCanvasLayout, Widget, WidgetType } from '@janus/schema'
import { createWidget, type WidgetValue } from '@janus/widgets'
import { commitHistory, createHistory, redoHistory, undoHistory } from './history'
import { createEmptyLayout, deleteWidget, insertWidget, nextWidgetId, updateWidget, updateWidgetGeometry, type LayoutMutation } from './layout'
import { validateProject } from './project'

export type EditorMode = 'edit' | 'preview'

export function useEditorState() {
  const [history, setHistory] = useState(() => createHistory(createEmptyLayout()))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [snapToGrid, setSnapToGrid] = useState(false)
  const [mode, setMode] = useState<EditorMode>('edit')
  const [error, setError] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, WidgetValue>>({})
  const layout = history.present
  const loadLayout = useCallback((layout: LogicalCanvasLayout) => {
    setHistory(createHistory(layout))
    setSelectedId(null)
    setError(null)
    setValues({})
  }, [])

  const commit = useCallback((mutation: LayoutMutation) => {
    if (!mutation.ok) {
      setError(mutation.message)
      return false
    }
    try { validateProject(mutation.layout) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Invalid project.'); return false }
    setHistory((current) => commitHistory(current, mutation.layout))
    setError(null)
    return true
  }, [])

  const addWidget = useCallback((type: WidgetType, rect: Rect) => {
    const id = nextWidgetId(type, layout.widgets)
    const widget = createWidget(type, id, rect)
    if (!commit(insertWidget(layout, widget))) return false
    setSelectedId(id)
    return true
  }, [commit, layout])

  const changeGeometry = useCallback((id: string, rect: Rect) => commit(updateWidgetGeometry(layout, id, rect)), [commit, layout])
  const changeWidget = useCallback((id: string, updater: (widget: Widget) => Widget) => commit(updateWidget(layout, id, updater)), [commit, layout])
  const removeWidget = useCallback((id: string) => {
    if (!commit(deleteWidget(layout, id))) return false
    setSelectedId((current) => current === id ? null : current)
    setValues((current) => {
      const next = { ...current }
      delete next[id]
      return next
    })
    return true
  }, [commit, layout])

  const undo = useCallback(() => {
    setHistory((current) => undoHistory(current))
    setError(null)
  }, [])
  const redo = useCallback(() => {
    setHistory((current) => redoHistory(current))
    setError(null)
  }, [])

  const selectedWidget = useMemo(() => layout.widgets.find(({ id }) => id === selectedId) ?? null, [layout, selectedId])

  const triggerWidget = useCallback((widget: Widget, value?: WidgetValue) => {
    if (value !== undefined) setValues((current) => ({ ...current, [widget.id]: value }))
  }, [])

  return {
    layout,
    loadLayout,
    replaceLayout: (layout: LogicalCanvasLayout) => { const ok = commit({ ok: true, layout }); if (ok) { setSelectedId(null); setValues({}); } return ok },
    renameLayout: (name: string) => name === layout.name || commit({ ok: true, layout: { ...layout, name } }),
    selectedId: selectedWidget ? selectedId : null,
    selectedWidget,
    snapToGrid,
    mode,
    error,
    values,
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    addWidget,
    changeGeometry,
    changeWidget,
    removeWidget,
    selectWidget: setSelectedId,
    setSnapToGrid,
    setMode,
    setError,
    undo,
    redo,
    triggerWidget,
  }
}

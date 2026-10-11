import { useEffect, useState } from 'react'
import '@janus/design/tokens.css'
import type { WidgetType } from '@janus/schema'
import { TopBar } from './components/TopBar'
import { WidgetPalette } from './components/WidgetPalette'
import { EditorCanvas } from './components/EditorCanvas'
import { Inspector } from './components/Inspector'
import { BottomBar } from './components/BottomBar'
import { useEditorState } from './editor/useEditorState'
import './styles/glass.css'
import './styles/studio.css'

function App() {
  const editor = useEditorState()
  const [pendingType, setPendingType] = useState<WidgetType | null>(null)

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const editingText = target?.matches('input, textarea, select, [contenteditable="true"]')
      if (editingText || editor.mode !== 'edit') return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) editor.redo()
        else editor.undo()
        return
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && editor.selectedId) {
        event.preventDefault()
        editor.removeWidget(editor.selectedId)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [editor])

  return <div className="studio-shell">
    <TopBar />
    <div className="studio-body">
      <WidgetPalette onDragStart={setPendingType} onDragEnd={() => setPendingType(null)} disabled={editor.mode === 'preview'} />
      <EditorCanvas
        layout={editor.layout}
        selectedId={editor.selectedId}
        pendingType={pendingType}
        snapToGrid={editor.snapToGrid}
        mode={editor.mode}
        values={editor.values}
        canUndo={editor.canUndo}
        canRedo={editor.canRedo}
        onSelect={editor.selectWidget}
        onInsert={editor.addWidget}
        onGeometryChange={editor.changeGeometry}
        onTrigger={editor.triggerWidget}
        onToggleSnap={() => editor.setSnapToGrid((current) => !current)}
        onToggleMode={() => editor.setMode((current) => current === 'edit' ? 'preview' : 'edit')}
        onUndo={editor.undo}
        onRedo={editor.redo}
      />
      <Inspector widget={editor.selectedWidget} mode={editor.mode} error={editor.error} onGeometryChange={editor.changeGeometry} onWidgetChange={editor.changeWidget} onDelete={editor.removeWidget} />
    </div>
    <BottomBar />
  </div>
}

export default App

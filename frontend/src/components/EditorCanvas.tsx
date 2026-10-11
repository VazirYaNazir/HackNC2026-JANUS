import { CanvasToolbar } from './CanvasToolbar'
import { DevicePreview } from './DevicePreview'

export function EditorCanvas() {
  return <main className="editor-workspace" aria-label="Editor workspace">
    <CanvasToolbar />
    <div className="canvas-area"><DevicePreview /></div>
  </main>
}

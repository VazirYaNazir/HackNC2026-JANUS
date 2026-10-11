import '@janus/design/tokens.css'
import { TopBar } from './components/TopBar'
import { WidgetPalette } from './components/WidgetPalette'
import { EditorCanvas } from './components/EditorCanvas'
import { Inspector } from './components/Inspector'
import { BottomBar } from './components/BottomBar'
import './styles/glass.css'
import './styles/studio.css'

function App() {
  return <div className="studio-shell">
    <TopBar />
    <div className="studio-body">
      <WidgetPalette />
      <EditorCanvas />
      <Inspector />
    </div>
    <BottomBar />
  </div>
}

export default App

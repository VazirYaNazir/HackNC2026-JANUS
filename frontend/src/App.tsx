import { TopBar } from './components/TopBar'
import { Sidebar } from './components/Sidebar'
import { Canvas } from './components/Canvas'
import { BottomBar } from './components/BottomBar'
import './App.css'

function App() {
  return <div className="studio-shell">
    <TopBar />
    <div className="studio-body">
      <Sidebar side="left" />
      <Canvas />
      <Sidebar side="right" />
    </div>
    <BottomBar />
  </div>
}

export default App

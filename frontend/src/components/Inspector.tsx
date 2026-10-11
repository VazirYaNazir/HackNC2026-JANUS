import { useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Icon } from '@janus/design'

const tabs = ['General', 'Style', 'Data', 'Logic'] as const
type Tab = typeof tabs[number]

export function Inspector() {
  const [active, setActive] = useState<Tab>('General')
  function moveTab(event: KeyboardEvent<HTMLButtonElement>) {
    const current = tabs.indexOf(active)
    const next = event.key === 'ArrowRight' ? (current + 1) % tabs.length : event.key === 'ArrowLeft' ? (current - 1 + tabs.length) % tabs.length : -1
    if (next < 0) return
    event.preventDefault()
    setActive(tabs[next])
    document.getElementById(`inspector-${tabs[next]}`)?.focus()
  }
  return <aside className="inspector studio-glass" aria-label="Inspector">
    <div className="panel-heading"><div><span className="panel-eyebrow">SELECTION</span><h2>Inspector</h2></div><Icon name="settings" size={18} /></div>
    <div className="inspector-tabs" role="tablist" aria-label="Inspector sections">{tabs.map((tab) => <button id={`inspector-${tab}`} type="button" role="tab" aria-selected={active === tab} aria-controls="inspector-panel" tabIndex={active === tab ? 0 : -1} onKeyDown={moveTab} onClick={() => setActive(tab)} key={tab}>{tab}</button>)}</div>
    <div id="inspector-panel" className="inspector-empty" role="tabpanel" aria-labelledby={`inspector-${active}`}>
      <span className="inspector-empty-icon"><Icon name="help" size={24} /></span>
      <p>Select a widget to edit its properties.</p>
    </div>
  </aside>
}

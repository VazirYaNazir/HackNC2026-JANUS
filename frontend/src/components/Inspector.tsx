import { useState, type FocusEvent, type KeyboardEvent } from 'react'
import { ICONS, Icon, type Rect } from '@janus/design'
import type { Widget } from '@janus/schema'
import { isDialWidget, isInputWidget } from '@janus/widgets'
import type { EditorMode } from '../editor/useEditorState'

const tabs = ['General', 'Style', 'Data', 'Logic'] as const
const colors = ['amber', 'cyan', 'green', 'graphite', 'coral'] as const
const sources = ['cpu.percent', 'mem.percent', 'mem.used_mb', 'cpu.temperature', 'volume.percent', 'process.tree', 'screen.frame', 'native.metrics.status']
type Tab = typeof tabs[number]
type GeometryKey = keyof Rect

export function Inspector({ widget, mode, error, onGeometryChange, onWidgetChange, onDelete }: {
  widget: Widget | null
  mode: EditorMode
  error: string | null
  onGeometryChange: (id: string, rect: Rect) => boolean
  onWidgetChange: (id: string, updater: (widget: Widget) => Widget) => boolean
  onDelete: (id: string) => boolean
}) {
  const [active, setActive] = useState<Tab>('General')

  function moveTab(event: KeyboardEvent<HTMLButtonElement>) {
    const current = tabs.indexOf(active)
    const next = event.key === 'ArrowRight' ? (current + 1) % tabs.length : event.key === 'ArrowLeft' ? (current - 1 + tabs.length) % tabs.length : -1
    if (next < 0) return
    event.preventDefault()
    setActive(tabs[next])
    document.getElementById(`inspector-${tabs[next]}`)?.focus()
  }

  function commitLabel(event: FocusEvent<HTMLInputElement>) {
    if (!widget || event.currentTarget.value === widget.label || !event.currentTarget.value.trim()) {
      event.currentTarget.value = widget?.label ?? ''
      return
    }
    const label = event.currentTarget.value.trim()
    onWidgetChange(widget.id, (current) => ({ ...current, label }))
    event.currentTarget.value = label
  }

  function commitGeometry(key: GeometryKey, event: FocusEvent<HTMLInputElement>) {
    if (!widget) return
    const value = Number(event.currentTarget.value)
    const rect = { x: widget.x, y: widget.y, w: widget.w, h: widget.h, [key]: value }
    if (!onGeometryChange(widget.id, rect)) event.currentTarget.value = String(widget[key])
  }

  const disabled = mode === 'preview'

  return <aside className="inspector studio-glass" aria-label="Inspector">
    <div className="panel-heading"><div><span className="panel-eyebrow">SELECTION</span><h2>Inspector</h2></div><Icon name="settings" size={18} /></div>
    <div className="inspector-tabs" role="tablist" aria-label="Inspector sections">{tabs.map((tab) => <button id={`inspector-${tab}`} type="button" role="tab" aria-selected={active === tab} aria-controls="inspector-panel" tabIndex={active === tab ? 0 : -1} onKeyDown={moveTab} onClick={() => setActive(tab)} key={tab}>{tab}</button>)}</div>
    {!widget ? <div id="inspector-panel" className="inspector-empty" role="tabpanel" aria-labelledby={`inspector-${active}`}>
      <span className="inspector-empty-icon"><Icon name="help" size={24} /></span>
      <p>Select a widget to edit its properties.</p>
    </div> : <div id="inspector-panel" className="inspector-content" role="tabpanel" aria-labelledby={`inspector-${active}`}>
      <div className="inspector-widget-name"><span>{widget.type}</span><strong>{widget.label}</strong></div>
      {active === 'General' && <>
        <label className="inspector-field"><span>Label</span><input key={`${widget.id}-${widget.label}`} defaultValue={widget.label} disabled={disabled} onBlur={commitLabel} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} /></label>
        <div className="geometry-fields">{(['x', 'y', 'w', 'h'] as const).map((key) => <label className="inspector-field" key={key}><span>{key.toUpperCase()}</span><input key={`${widget.id}-${key}-${widget[key]}`} type="number" step="1" defaultValue={widget[key]} disabled={disabled} onBlur={(event) => commitGeometry(key, event)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} /></label>)}</div>
        <button className="delete-widget" type="button" disabled={disabled} onClick={() => onDelete(widget.id)}><Icon name="trash" size={15} />Delete widget</button>
      </>}
      {active === 'Style' && <>
        <label className="inspector-field"><span>Accent role</span><select value={widget.color ?? 'graphite'} disabled={disabled} onChange={(event) => onWidgetChange(widget.id, (current) => ({ ...current, color: event.target.value as Widget['color'] }))}>{colors.map((color) => <option key={color} value={color}>{color}</option>)}</select></label>
        <label className="inspector-field"><span>Icon</span><select value={widget.icon ?? ''} disabled={disabled} onChange={event => onWidgetChange(widget.id, current => ({ ...current, icon: event.target.value || undefined }))}><option value="">None</option>{Object.keys(ICONS).map(icon => <option key={icon} value={icon}>{icon}</option>)}</select></label>
        {widget.type === 'toggle' && <label className="inspector-field"><span>Toggle style</span><select value={widget.variant ?? 'switch'} disabled={disabled} onChange={event => onWidgetChange(widget.id, current => isInputWidget(current) ? { ...current, variant: event.target.value as 'switch' | 'push' } : current)}><option value="switch">Switch</option><option value="push">Push button</option></select></label>}
      </>}
      {active === 'Data' && (isDialWidget(widget) ? <>
        <InspectorTextField label="Source ID" value={widget.source} disabled={disabled} onCommit={(value) => onWidgetChange(widget.id, (current) => isDialWidget(current) ? { ...current, source: value } : current)} />
        <InspectorTextField label="Unit" value={widget.unit ?? ''} disabled={disabled} onCommit={(value) => onWidgetChange(widget.id, (current) => isDialWidget(current) ? { ...current, unit: value || undefined } : current)} />
        <label className="inspector-field"><span>Built-in data</span><select aria-label="Built-in data" value={sources.includes(widget.source) ? widget.source : ''} disabled={disabled} onChange={event => { const source = event.target.value; if (source) onWidgetChange(widget.id, current => isDialWidget(current) ? { ...current, source } : current) }}><option value="">Custom source</option>{sources.map(source => <option key={source}>{source}</option>)}</select></label>
        {(['warn', 'critical'] as const).map(key => <InspectorTextField key={key} label={key === 'warn' ? 'Warning threshold' : 'Critical threshold'} value={widget[key]?.toString() ?? ''} disabled={disabled} onCommit={value => { if (value && !Number.isFinite(Number(value))) return false; return onWidgetChange(widget.id, current => isDialWidget(current) ? { ...current, [key]: value ? Number(value) : undefined } : current) }} />)}
      </> : <p className="inspector-note">This control has no data source binding.</p>)}
      {active === 'Logic' && (isInputWidget(widget) ? <>
        <InspectorTextField label="Action ID" value={widget.action} disabled={disabled} onCommit={(value) => onWidgetChange(widget.id, (current) => isInputWidget(current) ? { ...current, action: value } : current)} />
        <label className="inspector-field"><span>Built-in action</span><select aria-label="Built-in action" value={widget.action.startsWith('native_') ? widget.action : ''} disabled={disabled} onChange={event => onWidgetChange(widget.id, current => isInputWidget(current) ? { ...current, action: event.target.value, confirm: false } : current)}><option value="">Generated / custom action</option>{(widget.type === 'slider' || widget.type === 'knob' ? ['native_volume', 'native_scroll'] : widget.type === 'joystick' ? ['native_mouse'] : widget.type === 'button' ? ['native_left_click', 'native_right_click'] : []).map(action => <option key={action}>{action}</option>)}</select></label>
        {(widget.type === 'slider' || widget.type === 'knob') && (['min', 'max', 'step'] as const).map(key => <InspectorTextField key={key} label={key === 'min' ? 'Minimum' : key === 'max' ? 'Maximum' : 'Step'} value={widget[key]?.toString() ?? ''} disabled={disabled} onCommit={value => { if (value && !Number.isFinite(Number(value))) return false; return onWidgetChange(widget.id, current => isInputWidget(current) ? { ...current, [key]: value ? Number(value) : undefined } : current) }} />)}
        <label className="inspector-check"><input type="checkbox" checked={widget.confirm ?? false} disabled={disabled} onChange={(event) => onWidgetChange(widget.id, (current) => isInputWidget(current) ? { ...current, confirm: event.target.checked } : current)} /><span>Require confirmation</span></label>
      </> : <p className="inspector-note">Display widgets do not define actions.</p>)}
      {error && <p className="inspector-error" role="alert">{error}</p>}
      {disabled && <p className="inspector-note">Inspector editing is disabled in Preview mode.</p>}
    </div>}
  </aside>
}

function InspectorTextField({ label, value, disabled, onCommit }: { label: string; value: string; disabled: boolean; onCommit: (value: string) => boolean | void }) {
  return <label className="inspector-field"><span>{label}</span><input key={`${label}-${value}`} defaultValue={value} disabled={disabled} onBlur={(event) => { const next = event.currentTarget.value.trim(); if (next !== value && onCommit(next) === false) event.currentTarget.value = value }} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} /></label>
}

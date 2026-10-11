import { useMemo, useState } from 'react'
import { Icon } from '@janus/design'
import { WIDGETS, WIDGET_TYPES } from '@janus/widgets'
import { WidgetPaletteItem } from './WidgetPaletteItem'

const categories = ['Controls', 'Displays'] as const

export function WidgetPalette() {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState<string[]>([])
  const available = useMemo(() => WIDGET_TYPES.flatMap((type) => {
    const definition = WIDGETS[type]
    return definition && definition.label.toLowerCase().includes(query.trim().toLowerCase()) ? [{ type, definition }] : []
  }), [query])

  return <aside className="widget-palette studio-glass" aria-label="Widget palette">
    <div className="panel-heading"><div><span className="panel-eyebrow">LIBRARY</span><h2>Components</h2></div><span className="panel-count">{available.length}</span></div>
    <label className="palette-search"><Icon name="search" size={15} /><span className="sr-only">Search components</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search components" /></label>
    <div className="palette-scroll">
      {categories.map((category) => {
        const items = available.filter(({ definition }) => definition.category === category)
        if (!items.length) return null
        const open = !collapsed.includes(category)
        return <section className="palette-category" key={category}>
          <button className="category-heading" type="button" aria-expanded={open} aria-controls={`palette-${category}`} onClick={() => setCollapsed((current) => open ? [...current, category] : current.filter((item) => item !== category))}>
            <span className={`category-chevron ${open ? 'is-open' : ''}`}>›</span>{category}<span>{items.length}</span>
          </button>
          <div id={`palette-${category}`} className="palette-items" role="list" hidden={!open}>{items.map(({ type, definition }) => <WidgetPaletteItem key={type} type={type} definition={definition} />)}</div>
        </section>
      })}
      {!available.length && <p className="palette-empty" role="status">No implemented components match “{query}”.</p>}
    </div>
    <div className="palette-note"><Icon name="lock" size={14} /><span>Placement unlocks in Milestone 3</span></div>
  </aside>
}

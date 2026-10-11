import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { normalizeLayout } from '@janus/design'
import type { LegacyGridLayout } from '@janus/schema'
import { PanelGrid } from '@janus/widgets'
import { CanvasToolbar } from '../src/components/CanvasToolbar'
import { DevicePreview } from '../src/components/DevicePreview'

const legacy: LegacyGridLayout = {
  schemaVersion: 1,
  name: 'Legacy',
  columns: 4,
  widgets: [{ id: 'w1', type: 'button', label: 'Run', action: '', x: 1, y: 2, w: 2, h: 1 }],
}

describe('shared rendering', () => {
  it('renders legacy and normalized layouts with identical geometry', () => {
    const fromLegacy = renderToStaticMarkup(<PanelGrid layout={legacy} />)
    const fromLogical = renderToStaticMarkup(<PanelGrid layout={normalizeLayout(legacy)} />)
    expect(fromLegacy).toBe(fromLogical)
    expect(fromLogical).toContain('left:25.833333333333336%')
    expect(fromLogical).toContain('width:48.333333333333336%')
  })
})

describe('edit and preview rendering', () => {
  const layout = normalizeLayout(legacy)
  const noop = vi.fn()

  it('shows editor handles only in Edit mode', () => {
    const edit = renderToStaticMarkup(<DevicePreview layout={layout} selectedId="w1" pendingType={null} snapToGrid={false} mode="edit" values={{}} onSelect={noop} onInsert={() => true} onGeometryChange={() => true} onTrigger={noop} />)
    const preview = renderToStaticMarkup(<DevicePreview layout={layout} selectedId="w1" pendingType={null} snapToGrid={false} mode="preview" values={{}} onSelect={noop} onInsert={() => true} onGeometryChange={() => true} onTrigger={noop} />)
    expect(edit).toContain('resize-handle')
    expect(preview).not.toContain('resize-handle')
    expect(preview).toContain('is-preview')
  })

  it('disables editing controls in Preview mode', () => {
    const toolbar = renderToStaticMarkup(<CanvasToolbar snapToGrid={false} mode="preview" canUndo canRedo onToggleSnap={noop} onToggleMode={noop} onUndo={noop} onRedo={noop} />)
    expect(toolbar.match(/disabled=""/g)).toHaveLength(3)
    expect(toolbar).toContain('Preview')
  })
})

import { describe, expect, it } from 'vitest'
import type { Widget } from '@janus/schema'
import { commitHistory, createHistory, redoHistory, undoHistory } from '../src/editor/history'
import { moveRect, resizeRect, viewportDeltaToLogical } from '../src/editor/geometry'
import { createEmptyLayout, deleteWidget, insertWidget, nextWidgetId, updateWidget, updateWidgetGeometry } from '../src/editor/layout'

const widget = (id: string, x = 0, y = 0): Widget => ({ id, type: 'button', label: id, action: '', x, y, w: 81, h: 62 })

describe('editor layout mutations', () => {
  it('creates stable unique IDs', () => {
    expect(nextWidgetId('button', [widget('button-1'), widget('button-3')])).toBe('button-2')
  })

  it('accepts valid insertion and rejects collision and overflow', () => {
    const empty = createEmptyLayout()
    const first = insertWidget(empty, widget('button-1'))
    expect(first.ok).toBe(true)
    if (!first.ok) return
    expect(insertWidget(first.layout, widget('button-2', 20, 10))).toMatchObject({ ok: false })
    expect(insertWidget(first.layout, { ...widget('button-2'), x: 300, w: 81 })).toMatchObject({ ok: false })
  })

  it('rejects invalid movement and resizing without changing the committed layout', () => {
    const inserted = insertWidget(createEmptyLayout(), widget('button-1'))
    if (!inserted.ok) throw new Error(inserted.message)
    const collisionTarget = insertWidget(inserted.layout, widget('button-2', 93, 0))
    if (!collisionTarget.ok) throw new Error(collisionTarget.message)
    expect(updateWidgetGeometry(collisionTarget.layout, 'button-2', { x: 40, y: 0, w: 81, h: 62 })).toMatchObject({ ok: false })
    expect(updateWidgetGeometry(collisionTarget.layout, 'button-2', { x: 93, y: 0, w: 300, h: 62 })).toMatchObject({ ok: false })
    expect(collisionTarget.layout.widgets[1]).toMatchObject({ x: 93, y: 0, w: 81, h: 62 })
  })

  it('keeps free movement independent of the rendered canvas size', () => {
    const smallViewport = viewportDeltaToLogical({ x: 18, y: 39 }, { width: 180, height: 390 }, { width: 360, height: 780 })
    const largeViewport = viewportDeltaToLogical({ x: 36, y: 78 }, { width: 360, height: 780 }, { width: 360, height: 780 })
    expect(smallViewport).toEqual({ x: 36, y: 78 })
    expect(largeViewport).toEqual(smallViewport)
    expect(moveRect({ x: 10, y: 20, w: 81, h: 62 }, smallViewport, false)).toEqual({ x: 46, y: 98, w: 81, h: 62 })
  })

  it('snaps movement to the legacy grid only when requested', () => {
    const origin = { x: 0, y: 0, w: 81, h: 62 }
    expect(moveRect(origin, { x: 80, y: 70 }, false)).toMatchObject({ x: 80, y: 70 })
    expect(moveRect(origin, { x: 80, y: 70 }, true)).toMatchObject({ x: 93, y: 74 })
  })

  it('enforces minimum free sizes and registry presets while snapping', () => {
    const origin = { x: 0, y: 0, w: 174, h: 62 }
    expect(resizeRect('button', origin, { x: -500, y: -500 }, false)).toEqual({ x: 0, y: 0, w: 81, h: 62 })
    expect(resizeRect('button', origin, { x: 80, y: 60 }, true)).toEqual({ x: 0, y: 0, w: 174, h: 136 })
  })

  it('updates supported widget properties without changing geometry', () => {
    const inserted = insertWidget(createEmptyLayout(), widget('button-1'))
    if (!inserted.ok) throw new Error(inserted.message)
    const changed = updateWidget(inserted.layout, 'button-1', (current) => ({ ...current, label: 'Launch', color: 'coral' }))
    if (!changed.ok) throw new Error(changed.message)
    expect(changed.layout.widgets[0]).toMatchObject({ label: 'Launch', color: 'coral', x: 0, y: 0, w: 81, h: 62 })
  })

  it('supports undo and redo for committed insertion, movement, resizing, and deletion', () => {
    const initial = createEmptyLayout()
    const inserted = insertWidget(initial, widget('button-1'))
    if (!inserted.ok) throw new Error(inserted.message)
    const moved = updateWidgetGeometry(inserted.layout, 'button-1', { x: 93, y: 0, w: 81, h: 62 })
    if (!moved.ok) throw new Error(moved.message)
    const resized = updateWidgetGeometry(moved.layout, 'button-1', { x: 93, y: 0, w: 174, h: 62 })
    if (!resized.ok) throw new Error(resized.message)
    const deleted = deleteWidget(resized.layout, 'button-1')
    if (!deleted.ok) throw new Error(deleted.message)

    let history = createHistory(initial)
    for (const layout of [inserted.layout, moved.layout, resized.layout, deleted.layout]) history = commitHistory(history, layout)
    history = undoHistory(history)
    expect(history.present.widgets[0]).toMatchObject({ x: 93, w: 174 })
    history = undoHistory(history)
    expect(history.present.widgets[0]).toMatchObject({ x: 93, w: 81 })
    history = redoHistory(history)
    expect(history.present.widgets[0]).toMatchObject({ x: 93, w: 174 })
    history = commitHistory(history, moved.layout)
    expect(history.future).toEqual([])
  })
})

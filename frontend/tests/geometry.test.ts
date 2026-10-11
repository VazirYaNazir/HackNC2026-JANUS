import { describe, expect, it } from 'vitest'
import {
  LOGICAL_CANVAS,
  LayoutGeometryError,
  gridRectToLogical,
  normalizeLayout,
  rectsOverlap,
  snapPositionToGrid,
  validatePlacement,
} from '@janus/design'
import type { LegacyGridLayout, LogicalCanvasLayout, Widget } from '@janus/schema'

const button = (id: string, x: number, y: number, w: number, h: number): Widget => ({
  id, type: 'button', label: id, action: '', x, y, w, h,
})

const legacy = (widgets: Widget[]): LegacyGridLayout => ({
  schemaVersion: 1,
  name: 'Legacy',
  columns: 4,
  widgets,
})

describe('legacy migration', () => {
  it('converts four-column grid cells into the agreed logical geometry', () => {
    const normalized = normalizeLayout(legacy([button('legacy-button', 1, 2, 2, 1)]))
    expect(normalized.widgets[0]).toMatchObject({ id: 'legacy-button', x: 93, y: 148, w: 174, h: 62 })
    expect(normalized.canvas).toEqual(LOGICAL_CANVAS)
  })

  it('identifies an out-of-bounds legacy widget and its original geometry', () => {
    expect(() => normalizeLayout(legacy([button('too-low', 0, 10, 1, 1)]))).toThrowError(
      'Legacy widget "too-low" has invalid geometry x=0, y=740, w=81, h=62: extends beyond the 360 × 780 canvas.',
    )
    try {
      normalizeLayout(legacy([button('too-wide', 3, 0, 2, 1)]))
    } catch (error) {
      expect(error).toBeInstanceOf(LayoutGeometryError)
      expect(error).toMatchObject({ code: 'out-of-bounds', widgetId: 'too-wide' })
    }
  })

  it('rejects overlapping legacy widgets without rearranging them', () => {
    expect(() => normalizeLayout(legacy([
      button('first', 0, 0, 2, 1),
      button('second', 1, 0, 2, 1),
    ]))).toThrow(/Widget "first".*overlaps widget "second"/)
  })
})

describe('normalization', () => {
  it('returns a valid version 2 layout by identity without converting it again', () => {
    const layout: LogicalCanvasLayout = {
      schemaVersion: 2,
      coordinateSpace: 'logical',
      canvas: { ...LOGICAL_CANVAS },
      name: 'Logical',
      widgets: [button('logical-button', 93, 148, 174, 62)],
    }
    const once = normalizeLayout(layout)
    const twice = normalizeLayout(once)
    expect(once).toBe(layout)
    expect(twice).toBe(layout)
    expect(twice.widgets[0]).toMatchObject({ x: 93, y: 148, w: 174, h: 62 })
  })
})

describe('collision, boundaries, and snapping', () => {
  it('allows touching edges but rejects intersecting interiors', () => {
    expect(rectsOverlap({ x: 0, y: 0, w: 50, h: 50 }, { x: 50, y: 0, w: 50, h: 50 })).toBe(false)
    expect(rectsOverlap({ x: 0, y: 0, w: 50, h: 50 }, { x: 49, y: 0, w: 50, h: 50 })).toBe(true)
  })

  it('rejects negative and overflowing geometry', () => {
    expect(validatePlacement({ x: -1, y: 0, w: 20, h: 20 }, [])).toMatchObject({ valid: false, reason: 'out-of-bounds' })
    expect(validatePlacement({ x: 340, y: 0, w: 21, h: 20 }, [])).toMatchObject({ valid: false, reason: 'out-of-bounds' })
    expect(validatePlacement({ x: 0, y: 761, w: 20, h: 20 }, [])).toMatchObject({ valid: false, reason: 'out-of-bounds' })
  })

  it('preserves the legacy four-column geometry when snapping', () => {
    expect(gridRectToLogical({ x: 2, y: 3, w: 1, h: 2 })).toEqual({ x: 186, y: 222, w: 81, h: 136 })
    expect(snapPositionToGrid({ x: 181, y: 226, w: 81, h: 62 })).toEqual({ x: 186, y: 222, w: 81, h: 62 })
  })
})

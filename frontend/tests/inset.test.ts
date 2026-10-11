import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { GRID, LOGICAL_CANVAS, logicalRectStyle, normalizeLayout, paddedCanvas, paddedRectStyle, screenFractionToCanvas, snapPositionToGrid, validatePlacement } from '@janus/design'
import type { LogicalCanvasLayout, Widget } from '@janus/schema'
import { viewportDeltaToLogical } from '../src/editor/geometry'

const widget = (over: Partial<Widget> = {}): Widget => ({ id: 'w', type: 'button', label: 'W', action: 'compute', x: 0, y: 0, w: 168, h: 62, ...over }) as Widget
const percent = (value: string) => Number.parseFloat(value)

describe('the shared edge gap', () => {
  it('is one constant next to the other grid constants', () => {
    expect(GRID).toEqual({ columns: 4, rowHeight: 62, gap: 12, padding: 18 })
  })

  it('is what the phone (PWA) pads its panel and gallery with, and what the builder draws with', () => {
    for (const file of ['../../PWA/src/screens/PanelScreen.tsx', '../../PWA/src/screens/Gallery.tsx']) {
      expect(readFileSync(new URL(file, import.meta.url), 'utf8')).toContain('padding: `${GRID.padding}px`')
    }
    const builder = readFileSync(new URL('../src/components/DevicePreview.tsx', import.meta.url), 'utf8')
    expect(builder).toContain('GRID.padding')
    expect(builder).toContain('paddedRectStyle(')
    expect(builder).not.toContain('logicalRectStyle(')    // nothing may position widgets flush against the frame again
  })

  it('makes the builder screen the canvas plus the gap on every side', () => {
    expect(paddedCanvas()).toEqual({ width: 396, height: 816 })
    expect(paddedCanvas({ width: 100, height: 50 }, 5)).toEqual({ width: 110, height: 60 })
  })
})

describe('a widget at x=0 / y=0', () => {
  it('sits inset from the frame edge in the builder', () => {
    const style = paddedRectStyle({ x: 0, y: 0, w: 168, h: 62 })
    expect(percent(style.left)).toBeCloseTo((18 / 396) * 100, 6)
    expect(percent(style.top)).toBeCloseTo((18 / 816) * 100, 6)
    expect(percent(style.left)).toBeGreaterThan(0)
    expect(percent(style.top)).toBeGreaterThan(0)
  })

  it('is flush with the edge in the old unpadded math, which the builder no longer uses', () => {
    expect(logicalRectStyle({ x: 0, y: 0, w: 168, h: 62 }).left).toBe('0%')
  })

  it('lines up with the phone: same pixel position when the phone is the reference width, and within half a percent of it for real phones', () => {
    const phoneLeft = (screenWidth: number, x: number) => GRID.padding + (x / LOGICAL_CANVAS.width) * (screenWidth - 2 * GRID.padding)
    const builderLeft = (screenWidth: number, x: number) => (percent(paddedRectStyle({ x, y: 0, w: 10, h: 10 }).left) / 100) * screenWidth
    for (const x of [0, 93, 186, 267]) {
      expect(builderLeft(396, x)).toBeCloseTo(phoneLeft(396, x), 6)                       // exact at 396 px
      for (const width of [360, 375, 390, 412, 430]) {
        expect(Math.abs(builderLeft(width, x) - phoneLeft(width, x)) / width).toBeLessThan(0.005)
      }
    }
  })

  it('keeps widget width proportional to the canvas, so sizes match the phone too', () => {
    const style = paddedRectStyle({ x: 0, y: 0, w: 168, h: 62 })
    expect(percent(style.width)).toBeCloseTo((168 / 396) * 100, 6)
    expect(percent(style.height)).toBeCloseTo((62 / 816) * 100, 6)
  })

  it('ends exactly the gap away from the right edge when it spans to the canvas edge', () => {
    const style = paddedRectStyle({ x: 270, y: 0, w: 90, h: 62 })
    expect(percent(style.left) + percent(style.width)).toBeCloseTo(((360 + 18) / 396) * 100, 6)
  })
})

describe('the edge gap cannot be used', () => {
  const at = (fx: number, fy: number) => screenFractionToCanvas({ x: fx, y: fy })

  it('maps the canvas corners onto the padded screen, and the screen corners outside the canvas', () => {
    expect(at(18 / 396, 18 / 816)).toEqual({ x: 0, y: 0 })
    expect(at((18 + 360) / 396, (18 + 780) / 816)).toEqual({ x: 360, y: 780 })
    const corner = at(0, 0)
    expect(corner.x).toBe(-18)
    expect(corner.y).toBe(-18)
  })

  it('rejects a drop whose rect reaches into any of the four gaps', () => {
    const size = { w: 168, h: 62 }
    const dropCentredOn = (fx: number, fy: number) => {
      const p = at(fx, fy)
      return validatePlacement({ x: p.x - size.w / 2, y: p.y - size.h / 2, ...size }, [])
    }
    expect(dropCentredOn(0.02, 0.5).reason).toBe('out-of-bounds')        // left gap
    expect(dropCentredOn(0.98, 0.5).reason).toBe('out-of-bounds')        // right gap
    expect(dropCentredOn(0.5, 0.01).reason).toBe('out-of-bounds')        // top gap
    expect(dropCentredOn(0.5, 0.995).reason).toBe('out-of-bounds')       // bottom gap
    expect(dropCentredOn(0.5, 0.5).valid).toBe(true)                      // the middle is fine
  })

  it('still allows exactly x=0 / y=0, but nothing past it', () => {
    expect(validatePlacement({ x: 0, y: 0, w: 168, h: 62 }, []).valid).toBe(true)
    expect(validatePlacement({ x: -1, y: 0, w: 168, h: 62 }, []).reason).toBe('out-of-bounds')
    expect(validatePlacement({ x: 0, y: -1, w: 168, h: 62 }, []).reason).toBe('out-of-bounds')
    expect(validatePlacement({ x: 360 - 168 + 1, y: 0, w: 168, h: 62 }, []).reason).toBe('out-of-bounds')
    expect(validatePlacement({ x: 360 - 168, y: 0, w: 168, h: 62 }, []).valid).toBe(true)
  })

  it('snaps a drop that lands near the left gap back to the first column, not into the gap', () => {
    const p = at(0.03, 0.2)
    const snapped = snapPositionToGrid({ x: p.x, y: p.y, w: 80, h: 62 })
    expect(snapped.x === 0).toBe(true)                                // -0 counts: JSON.stringify(-0) is "0"
    expect(validatePlacement({ ...snapped, y: 0 }, []).valid).toBe(true)
  })
})

describe('dragging on the padded screen', () => {
  // DevicePreview converts a pointer delta using the screen's pixel size and paddedCanvas(): the same scale in both axes.
  const delta = (px: { x: number; y: number }, screenPx: { width: number; height: number }) => viewportDeltaToLogical(px, screenPx, paddedCanvas())

  it('is 1:1 when the screen is drawn at its logical size', () => {
    expect(delta({ x: 20, y: 20 }, { width: 396, height: 816 })).toEqual({ x: 20, y: 20 })
  })

  it('scales with the screen: half-size screen, double the logical distance', () => {
    expect(delta({ x: 10, y: 10 }, { width: 198, height: 408 })).toEqual({ x: 20, y: 20 })
  })

  it('measures drag distance against the whole padded screen, not just the canvas', () => {
    // On a 304 px wide screen (the full-size preview), 30.4 px is 10% of the width, which is 39.6 of the 396 logical units across.
    expect(delta({ x: 30.4, y: 0 }, { width: 304, height: 626 }).x).toBeCloseTo(39.6, 6)
  })
})

describe('saved layouts are untouched', () => {
  it('keeps widget x/y exactly as saved, and does not mutate rects when styling them', () => {
    const rect = { x: 0, y: 0, w: 168, h: 62 }
    paddedRectStyle(rect)
    expect(rect).toEqual({ x: 0, y: 0, w: 168, h: 62 })
    const layout: LogicalCanvasLayout = { schemaVersion: 2, coordinateSpace: 'logical', canvas: { width: 360, height: 780 }, name: 'Desk', widgets: [widget(), widget({ id: 'v', x: 192, y: 74 })] }
    expect(normalizeLayout(layout)).toBe(layout)                       // identity: nothing converted or shifted
    expect(layout.canvas).toEqual({ width: 360, height: 780 })
    expect(layout.widgets.map((w) => [w.x, w.y])).toEqual([[0, 0], [192, 74]])
  })
})

describe('ring and shadow clipping', () => {
  const css = readFileSync(new URL('../src/styles/studio.css', import.meta.url), 'utf8')
  const rule = (selector: string) => css.split('\n').find((line) => line.startsWith(`${selector} {`)) ?? ''

  it('does not clip the phone screen any more', () => {
    expect(rule('.phone-screen')).toContain('overflow: visible')
    expect(rule('.phone-screen')).not.toContain('overflow: hidden')
  })

  it('draws the snap grid over the canvas area instead of the whole screen', () => {
    expect(css).not.toContain('.phone-screen.is-snapping')
    expect(rule('.snap-grid')).toContain('background-size: 25.8333% 9.4872%')
  })

  it('leaves room: the gap is bigger than the selection ring (outline 2px + offset 2px)', () => {
    const screenAtMinimumWidth = 196                                     // the compact phone frame, about the narrowest screen
    const gapPx = (GRID.padding / 396) * screenAtMinimumWidth
    expect(gapPx).toBeGreaterThan(4)
  })
})

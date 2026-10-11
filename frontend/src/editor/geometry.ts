import { snapPositionToGrid, type Rect } from '@janus/design'
import { getWidgetMinimumSize, snapWidgetSize } from '@janus/widgets'
import type { WidgetType } from '@janus/schema'

export interface Size {
  width: number
  height: number
}

export function viewportDeltaToLogical(
  clientDelta: { x: number; y: number },
  viewport: Size,
  canvas: Size,
): { x: number; y: number } {
  return {
    x: (clientDelta.x / viewport.width) * canvas.width,
    y: (clientDelta.y / viewport.height) * canvas.height,
  }
}

export function moveRect(origin: Rect, delta: { x: number; y: number }, snapToGrid: boolean): Rect {
  const rect = { ...origin, x: origin.x + delta.x, y: origin.y + delta.y }
  return snapToGrid ? snapPositionToGrid(rect) : rect
}

export function resizeRect(type: WidgetType, origin: Rect, delta: { x: number; y: number }, snapToGrid: boolean): Rect {
  const minimum = getWidgetMinimumSize(type)
  const rect = {
    ...origin,
    w: Math.max(minimum.w, origin.w + delta.x),
    h: Math.max(minimum.h, origin.h + delta.y),
  }
  return snapToGrid ? snapWidgetSize(type, rect) : rect
}

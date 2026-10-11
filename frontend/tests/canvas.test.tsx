// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DevicePreview } from '../src/components/DevicePreview';
import { WidgetPalette } from '../src/components/WidgetPalette';
import { createEmptyLayout } from '../src/editor/layout';
import { createWidget } from '@janus/widgets';
import './dom';

function event(type: string, x: number, y: number, dataTransfer?: object) {
  const value = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y });
  Object.defineProperty(value, 'pointerId', { value: 1 }); if (dataTransfer) Object.defineProperty(value, 'dataTransfer', { value: dataTransfer }); return value;
}
// The phone screen is the 360 x 780 canvas plus the shared edge gap (18) on every side: 396 x 816. Mocking it at that size in pixels
// keeps one pixel = one logical unit, and a pointer at pixel (px, py) is at canvas (px - 18, py - 18).
const bounds = { x: 0, y: 0, left: 0, top: 0, right: 396, bottom: 816, width: 396, height: 816, toJSON() {} };

describe('canvas interactions', () => {
  it('places dragged widgets only when inside the canvas and clears invalid previews', () => {
    const insert = vi.fn(() => true), select = vi.fn();
    const view = render(<DevicePreview layout={createEmptyLayout()} selectedId={null} pendingType="button" snapToGrid={false} mode="edit" values={{}} onSelect={select} onInsert={insert} onGeometryChange={() => true} onTrigger={() => {}} />);
    const canvas = view.container.querySelector('.phone-canvas')!; vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(bounds);
    const transfer = { dropEffect: '' }; fireEvent(canvas, event('dragover', 150, 90, transfer)); expect(view.container.querySelector('.placement-preview.is-valid')).not.toBeNull(); expect(transfer.dropEffect).toBe('copy');
    fireEvent(canvas, event('drop', 150, 90, transfer)); expect(insert).toHaveBeenCalledWith('button', expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }));
    fireEvent(canvas, event('dragover', -100, -100, transfer)); expect(transfer.dropEffect).toBe('none'); fireEvent(canvas, event('drop', -100, -100, transfer)); expect(insert).toHaveBeenCalledTimes(1);
    fireEvent.click(canvas); expect(select).toHaveBeenCalledWith(null);
  });
  it('moves and resizes with pointer capture, rejects out-of-bounds geometry and disables gestures in preview', () => {
    const widget = createWidget('button', 'button', { x: 0, y: 0, w: 170, h: 70 }), layout = { ...createEmptyLayout(), widgets: [widget] }, change = vi.fn(() => true);
    const props = { layout, selectedId: widget.id, pendingType: null, snapToGrid: false, values: {}, onSelect: vi.fn(), onInsert: () => true, onGeometryChange: change, onTrigger: vi.fn() };
    const view = render(<DevicePreview {...props} mode="edit" />); const canvas = view.container.querySelector('.phone-canvas')!; vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(bounds);
    const cell = view.container.querySelector('.editor-widget')!; fireEvent(cell, event('pointerdown', 10, 10)); fireEvent(cell, event('pointermove', 30, 30)); fireEvent(cell, event('pointerup', 30, 30)); expect(change).toHaveBeenLastCalledWith('button', { x: 20, y: 20, w: 170, h: 70 });
    fireEvent(cell, event('pointerdown', 10, 10)); fireEvent(cell, event('pointermove', 600, 900)); expect(cell.classList.contains('is-invalid')).toBe(true); fireEvent(cell, event('pointerup', 600, 900)); expect(change).toHaveBeenCalledTimes(1);
    const resize = screen.getByRole('button', { name: 'Resize Button' }); fireEvent(resize, event('pointerdown', 0, 0)); fireEvent(resize, event('pointermove', 20, 20)); fireEvent(resize, event('pointerup', 20, 20)); expect(change.mock.calls[1][1].w).toBe(190);
    view.rerender(<DevicePreview {...props} mode="preview" />); fireEvent(cell, event('pointerdown', 10, 10)); fireEvent(cell, event('pointermove', 30, 30)); fireEvent(cell, event('pointerup', 30, 30)); expect(change).toHaveBeenCalledTimes(2);
  });
  it('filters and collapses palette sections and attaches typed drag payloads', () => {
    const start = vi.fn(), end = vi.fn(); render(<WidgetPalette onDragStart={start} onDragEnd={end} />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'button' } }); expect(screen.getAllByRole('listitem')).toHaveLength(1);
    const transfer = { effectAllowed: '', setData: vi.fn() }; fireEvent(screen.getByRole('listitem'), event('dragstart', 0, 0, transfer)); expect(transfer.setData).toHaveBeenCalledWith('application/x-panel-widget', 'button'); expect(start).toHaveBeenCalledWith('button');
    fireEvent.dragEnd(screen.getByRole('listitem')); expect(end).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Controls/ })); expect(screen.queryByRole('listitem')).toBeNull(); fireEvent.click(screen.getByRole('button', { name: /Controls/ })); expect(screen.getByRole('listitem')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'not implemented' } }); expect(screen.getByRole('status').textContent).toContain('No implemented');
  });
});

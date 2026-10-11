// @vitest-environment jsdom
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { InputWidget, Widget, WidgetType } from '@janus/schema';
import { PanelGrid, WIDGET_TYPES, WidgetView, createWidget, getWidgetComponent, getWidgetDef, getWidgetMinimumSize, snapWidgetSize } from '@janus/widgets';
import { asNumber, formatNumber } from '../../shared/widgets/src/format';
import { polar, sweepPath } from '../../shared/widgets/src/arc';
import { levelOf } from '../../shared/widgets/src/level';
import { useHistory } from '../../shared/widgets/src/useHistory';
import { useThrottledSend } from '../../shared/widgets/src/useThrottledSend';
import { processRows } from '../../shared/widgets/src/processTree';
import './dom';

const widget = (type: WidgetType): Widget => createWidget(type, `${type}-1`, { x: 0, y: 0, w: 170, h: 170 });
const input = (type: WidgetType, action = ''): InputWidget => ({ ...widget(type), action }) as InputWidget;

describe('every widget in the registry', () => {
  it.each(WIDGET_TYPES)('%s renders in edit/live modes and releases observers', type => {
    const w = widget(type), onSelect = vi.fn(), onTrigger = vi.fn();
    const view = render(<WidgetView widget={w} mode="edit" onSelect={onSelect} onTrigger={onTrigger} />);
    expect(view.container.querySelector('.jw')).not.toBeNull();
    const select = screen.queryByRole('button', { name: `Select ${w.label}` });
    if (select) { fireEvent.click(select); expect(onSelect).toHaveBeenCalledWith(w.id); }
    expect(onTrigger).not.toHaveBeenCalled();
    view.rerender(<WidgetView widget={w} mode="live" value={42} selected />);
    expect(view.container.querySelector('.jw--live.jw--selected')).not.toBeNull();
    expect(screen.queryByRole('button', { name: `Select ${w.label}` })).toBeNull();
    expect(getWidgetMinimumSize(type).w).toBeGreaterThan(0);
    expect(snapWidgetSize(type, { x: 0, y: 0, w: 1, h: 1 }).w).toBeGreaterThan(1);
    view.unmount();
  });

  it('handles unknown and prototype-property widget types', () => {
    expect(getWidgetDef('toString')).toBeUndefined();
    expect(getWidgetComponent('unknown')).toBe(getWidgetComponent('toString'));
    expect(() => createWidget('unknown' as WidgetType, 'a', { x: 0, y: 0, w: 1, h: 1 })).toThrow();
    const rect = { x: 0, y: 0, w: 4, h: 5 };
    expect(snapWidgetSize('unknown', rect)).toBe(rect);
    render(<WidgetView widget={{ ...widget('number'), type: 'unknown' as WidgetType }} mode="live" />);
    expect(screen.getByText(/Unsupported/)).toBeTruthy();
  });

  it('selects a button in edit mode and sends no value in live mode', () => {
    const w = widget('button'), select = vi.fn(), trigger = vi.fn();
    const view = render(<WidgetView widget={w} mode="edit" onSelect={select} onTrigger={trigger} />);
    fireEvent.click(screen.getByRole('button', { name: w.label }));
    expect(select).toHaveBeenCalledWith(w.id); expect(trigger).not.toHaveBeenCalled();
    for (const state of ['running', 'done', 'error']) {
      view.rerender(<WidgetView widget={w} mode="live" state={state} onTrigger={trigger} />);
      expect(view.container.querySelector('.jw__hint')).not.toBeNull();
    }
    fireEvent.click(screen.getByRole('button', { name: w.label }));
    expect(trigger).toHaveBeenCalledWith(w, undefined);
  });

  it.each(['switch', 'push'] as const)('toggles the %s variant with the correct boolean', variant => {
    const w = { ...input('toggle'), variant }, trigger = vi.fn();
    const view = render(<WidgetView widget={w} mode="live" value={false} onTrigger={trigger} />);
    fireEvent.click(screen.getByRole('switch')); expect(trigger).toHaveBeenLastCalledWith(w, true);
    view.rerender(<WidgetView widget={w} mode="live" value={true} onTrigger={trigger} />);
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByRole('switch')); expect(trigger).toHaveBeenLastCalledWith(w, false);
  });

  it('commits custom sliders once on release and sends native sliders while dragging', () => {
    vi.useFakeTimers();
    const trigger = vi.fn(), w = input('slider');
    const view = render(<WidgetView widget={w} mode="live" onTrigger={trigger} />);
    fireEvent.change(screen.getByRole('slider'), { target: { value: '34' } });
    expect(trigger).not.toHaveBeenCalled();
    fireEvent.keyUp(screen.getByRole('slider')); expect(trigger).toHaveBeenLastCalledWith(w, 34);
    const native = { ...w, action: 'native_volume' };
    view.rerender(<WidgetView widget={native} mode="live" onTrigger={trigger} />);
    fireEvent.change(screen.getByRole('slider'), { target: { value: '70' } });
    act(() => vi.advanceTimersByTime(100)); expect(trigger).toHaveBeenLastCalledWith(native, 70);
    fireEvent.pointerUp(screen.getByRole('slider')); expect(trigger).toHaveBeenLastCalledWith(native, 70);
  });

  it('snaps and clamps keyboard knob values and leaves edit controls inert', () => {
    const w = { ...input('knob'), min: 10, max: 20, step: 2 }, trigger = vi.fn();
    const view = render(<WidgetView widget={w} mode="live" value={10} onTrigger={trigger} />);
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowUp' });
    expect(screen.getByRole('slider').getAttribute('aria-valuenow')).toBe('12');
    fireEvent.keyUp(screen.getByRole('slider')); expect(trigger).toHaveBeenLastCalledWith(w, 12);
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'End' }); fireEvent.blur(screen.getByRole('slider'));
    expect(trigger).toHaveBeenLastCalledWith(w, 20);
    view.rerender(<WidgetView widget={w} mode="edit" onTrigger={trigger} />); trigger.mockClear();
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowUp' }); expect(trigger).not.toHaveBeenCalled();
  });

  it.each(['up', 'right', 'down', 'left'])('sends d-pad direction %s', direction => {
    const w = widget('dpad'), trigger = vi.fn(); render(<WidgetView widget={w} mode="live" onTrigger={trigger} />);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${direction}$`, 'i') })); expect(trigger).toHaveBeenCalledWith(w, direction);
  });

  it('keeps native joystick movement alive, then sends zero on release, blur and unmount', () => {
    vi.useFakeTimers(); const w = input('joystick', 'native_mouse'), trigger = vi.fn();
    const view = render(<WidgetView widget={w} mode="live" onTrigger={trigger} />);
    const stick = screen.getByRole('group'); fireEvent.keyDown(stick, { key: 'ArrowUp' });
    act(() => vi.advanceTimersByTime(400)); expect(trigger).toHaveBeenLastCalledWith(w, { x: 0, y: 1 });
    expect(trigger.mock.calls.length).toBeGreaterThan(1);
    fireEvent.keyUp(stick, { key: 'ArrowUp' }); expect(trigger).toHaveBeenLastCalledWith(w, { x: 0, y: 0 });
    fireEvent.keyDown(stick, { key: 'ArrowRight' }); act(() => vi.advanceTimersByTime(150));
    fireEvent(window, new Event('blur')); expect(trigger).toHaveBeenLastCalledWith(w, { x: 0, y: 0 });
    fireEvent.keyDown(stick, { key: 'ArrowLeft' }); act(() => vi.advanceTimersByTime(150));
    view.unmount(); expect(trigger).toHaveBeenLastCalledWith(w, { x: 0, y: 0 });
  });

  it.each(['command', 'agent'] as const)('%s trims submissions, prevents empty requests and clears the field', type => {
    const w = widget(type), trigger = vi.fn(); render(<WidgetView widget={w} mode="live" state="answer from laptop" onTrigger={trigger} />);
    expect(screen.getByRole('button', { name: `Send ${w.label}` }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '  inspect apps  ' } });
    fireEvent.submit(screen.getByRole('textbox').closest('form')!);
    expect(trigger).toHaveBeenCalledWith(w, 'inspect apps'); expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('');
    if (type === 'agent') expect(screen.getByText('answer from laptop')).toBeTruthy();
  });

  it.each(['bar', 'gauge', 'ring', 'temperature', 'number', 'line'] as const)('%s handles missing data and escalates thresholds', type => {
    const w = { ...widget(type), warn: 50, critical: 80, unit: '%' }, view = render(<WidgetView widget={w} mode="live" />);
    expect(view.container.textContent).toContain('—');
    view.rerender(<WidgetView widget={w} mode="live" value={55} />); expect(view.container.querySelector('[class*="--warn"]')).not.toBeNull();
    view.rerender(<WidgetView widget={w} mode="live" value={90} />); expect(view.container.querySelector('[class*="--critical"]')).not.toBeNull();
    if (type === 'line') expect(view.container.querySelector('.jw__line')).not.toBeNull();
  });

  it('bounds logs and renders text without executing HTML', () => {
    const w = widget('log'), view = render(<WidgetView widget={w} mode="live" value="<script>bad()</script>" />);
    expect(view.container.querySelector('script')).toBeNull();
    for (let i = 0; i < 60; i++) view.rerender(<WidgetView widget={w} mode="live" value={`line-${i}`} />);
    expect(view.container.querySelectorAll('.jw__logline')).toHaveLength(50);
    expect(screen.queryByText('line-0')).toBeNull(); expect(screen.getByText('line-59')).toBeTruthy();
    fireEvent.scroll(screen.getByRole('log'));
  });

  it('recovers a screen stream only when a new frame arrives', () => {
    const w = widget('stream'), view = render(<WidgetView widget={w} mode="live" />);
    expect(screen.getByText(/Waiting for stream/)).toBeTruthy();
    view.rerender(<WidgetView widget={w} mode="live" value="data:image/jpeg;base64,first" />);
    fireEvent.error(view.container.querySelector('img')!); expect(screen.getByText('Stream offline')).toBeTruthy();
    view.rerender(<WidgetView widget={w} mode="live" value="data:image/jpeg;base64,second" />); expect(screen.getByText('LIVE')).toBeTruthy();
  });

  it('shows relevant process hierarchy, escaped names and the empty state', () => {
    const w = widget('processes'), value = JSON.stringify([
      { pid: 1, parent_pid: null, name: 'Parent', context: true, cpu_percent: null, ram_bytes: null },
      { pid: 2, parent_pid: 1, name: '<b>App</b>', context: false, cpu_percent: 12, ram_bytes: 104857600 },
    ]), view = render(<WidgetView widget={w} mode="live" value={value} />);
    expect(screen.getByRole('table')).toBeTruthy(); expect(view.container.querySelector('b')).toBeNull();
    expect(screen.getByText('100 MB')).toBeTruthy(); expect(view.container.querySelectorAll('tbody tr')).toHaveLength(2);
    view.rerender(<WidgetView widget={w} mode="live" value="[]" />); expect(screen.getByText(/No apps exceed/)).toBeTruthy();
  });

  it('blocks offline inputs but retains display readings and external volume updates', () => {
    const layout = { schemaVersion: 2 as const, coordinateSpace: 'logical' as const, canvas: { width: 360, height: 780 }, name: 'Test', widgets: [input('slider', 'native_volume')] };
    const trigger = vi.fn(), blocked = vi.fn();
    const view = render(<PanelGrid layout={layout} data={{ 'volume.percent': 63 }} readOnly onTrigger={trigger} onBlocked={blocked} />);
    expect(view.container.querySelector('input')?.value).toBe('63');
    fireEvent.click(screen.getByRole('button', { name: /unavailable/ })); expect(blocked).toHaveBeenCalled(); expect(trigger).not.toHaveBeenCalled();
    view.rerender(<PanelGrid layout={{ ...layout, canvas: { width: 0, height: 0 } }} />); expect(screen.getByRole('alert')).toBeTruthy();
  });
});

describe('widget utilities and hooks', () => {
  it('formats finite readings and computes threshold boundaries and arc geometry', () => {
    for (const value of [undefined, null, '1', Infinity, NaN]) expect(asNumber(value)).toBeUndefined();
    expect(asNumber(1.2)).toBe(1.2); expect(formatNumber(2)).toBe('2'); expect(formatNumber(1.26)).toBe('1.3');
    const w = widget('gauge'); expect(levelOf(w, 50, { warn: 50, critical: 80 })).toBe('warn'); expect(levelOf(w, 80, { critical: 80 })).toBe('critical');
    expect(levelOf(widget('button'), 100, { critical: 1 })).toBe('ok'); expect(levelOf(w, undefined)).toBe('ok');
    expect(polar(40, .5)).toEqual({ x: 50, y: 10 }); expect(sweepPath(40)).toContain(' A 40 40 0 1 1 ');
  });
  it('bounds history and ignores missing or repeated samples', () => {
    const hook = renderHook(({ value }) => useHistory(value, 2), { initialProps: { value: undefined as number | undefined } });
    expect(hook.result.current).toEqual([]); hook.rerender({ value: 1 }); hook.rerender({ value: 1 }); hook.rerender({ value: 2 }); hook.rerender({ value: 3 }); hook.rerender({ value: undefined });
    expect(hook.result.current).toEqual([2, 3]);
  });
  it('coalesces throttled values, flushes release immediately and cancels pending work on unmount', () => {
    vi.useFakeTimers(); const send = vi.fn(); const hook = renderHook(() => useThrottledSend(send, 100));
    act(() => { hook.result.current.push(1); hook.result.current.push(2); vi.advanceTimersByTime(100); }); expect(send).toHaveBeenLastCalledWith(2);
    act(() => { hook.result.current.push(3); hook.result.current.sendNow(0); vi.advanceTimersByTime(100); }); expect(send).toHaveBeenLastCalledWith(0);
    act(() => { hook.result.current.push(4); hook.result.current.push(5); }); const count = send.mock.calls.length;
    hook.unmount(); act(() => vi.advanceTimersByTime(1000)); expect(send).toHaveBeenCalledTimes(count);
  });
  it('rejects invalid process payloads and safely terminates cycles and self-parenting', () => {
    for (const value of [undefined, '{}', 'bad', '[{}]', JSON.stringify(new Array(513).fill({}))]) expect(processRows(value)).toBeNull();
    const row = { pid: 1, parent_pid: 2, name: 'A', context: false, cpu_percent: 1, ram_bytes: 1 };
    expect(processRows(JSON.stringify([row, { ...row, pid: 2, parent_pid: 1 }]))?.length).toBe(2);
    expect(processRows(JSON.stringify([{ ...row, parent_pid: 1 }]))?.[0].depth).toBe(0);
    expect(processRows(JSON.stringify([row, row]))).toBeNull();
  });
});

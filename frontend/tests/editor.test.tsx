// @vitest-environment jsdom
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Inspector } from '../src/components/Inspector';
import { ProjectDialog } from '../src/components/ProjectDialog';
import { TopBar } from '../src/components/TopBar';
import { useEditorState } from '../src/editor/useEditorState';
import { importProject, laptopProject, validateProject } from '../src/editor/project';
import { createEmptyLayout } from '../src/editor/layout';
import './dom';

describe('complete editor state', () => {
  it('inserts, selects, updates, deletes, undoes and redoes with validation', () => {
    const hook = renderHook(useEditorState);
    act(() => hook.result.current.addWidget('slider', { x: 0, y: 0, w: 170, h: 70 }));
    const id = hook.result.current.selectedId!; expect(id).toBe('slider-1');
    act(() => hook.result.current.changeWidget(id, w => ({ ...w, label: 'Volume' })));
    expect(hook.result.current.selectedWidget?.label).toBe('Volume');
    act(() => hook.result.current.changeWidget(id, w => ({ ...w, min: 10, max: 5 })));
    expect(hook.result.current.error).toContain('Minimum');
    act(() => hook.result.current.changeGeometry(id, { x: 350, y: 0, w: 170, h: 70 }));
    expect(hook.result.current.layout.widgets[0].x).toBe(0);
    act(() => hook.result.current.triggerWidget(hook.result.current.layout.widgets[0], 30));
    expect(hook.result.current.values[id]).toBe(30);
    act(() => hook.result.current.removeWidget(id)); expect(hook.result.current.values[id]).toBeUndefined(); expect(hook.result.current.selectedId).toBeNull();
    act(() => hook.result.current.undo()); expect(hook.result.current.layout.widgets).toHaveLength(1);
    act(() => hook.result.current.redo()); expect(hook.result.current.layout.widgets).toHaveLength(0);
    act(() => hook.result.current.renameLayout('Desk')); expect(hook.result.current.layout.name).toBe('Desk');
    act(() => hook.result.current.replaceLayout(laptopProject())); expect(hook.result.current.layout.widgets).toHaveLength(8);
    act(() => hook.result.current.undo()); expect(hook.result.current.layout.name).toBe('Desk');
    act(() => hook.result.current.loadLayout(laptopProject())); expect(hook.result.current.canUndo).toBe(false); expect(hook.result.current.values).toEqual({});
  });

  it('edits ranges, restores rejected numbers, binds native actions and prevents preview edits', () => {
    function Harness() {
      const editor = useEditorState();
      return <><button onClick={() => editor.addWidget('slider', { x: 0, y: 0, w: 170, h: 70 })}>Add</button><button onClick={() => editor.setMode('preview')}>Preview</button>
        <Inspector widget={editor.selectedWidget} mode={editor.mode} error={editor.error} onGeometryChange={editor.changeGeometry} onWidgetChange={editor.changeWidget} onDelete={editor.removeWidget} /></>;
    }
    render(<Harness />); expect(screen.getByText(/Select a widget/)).toBeTruthy(); fireEvent.click(screen.getByText('Add'));
    fireEvent.click(screen.getByRole('tab', { name: 'Logic' }));
    fireEvent.change(screen.getByLabelText('Built-in action'), { target: { value: 'native_volume' } });
    expect((screen.getByLabelText('Action ID') as HTMLInputElement).value).toBe('native_volume');
    fireEvent.change(screen.getByLabelText('Minimum'), { target: { value: '10' } }); fireEvent.blur(screen.getByLabelText('Minimum'));
    fireEvent.change(screen.getByLabelText('Maximum'), { target: { value: '5' } }); fireEvent.blur(screen.getByLabelText('Maximum'));
    expect(screen.getByRole('alert').textContent).toContain('Minimum'); expect((screen.getByLabelText('Maximum') as HTMLInputElement).value).toBe('');
    fireEvent.change(screen.getByLabelText('Step'), { target: { value: '0' } }); fireEvent.blur(screen.getByLabelText('Step')); expect(screen.getByRole('alert').textContent).toContain('Step');
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Logic' }), { key: 'ArrowLeft' }); expect(screen.getByRole('tab', { name: 'Data' }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByText('Preview')); fireEvent.click(screen.getByRole('tab', { name: 'General' }));
    expect(screen.getByLabelText('Label').hasAttribute('disabled')).toBe(true); expect(screen.getByRole('button', { name: 'Delete widget' }).hasAttribute('disabled')).toBe(true);
  });
});

describe('project import, export and branding', () => {
  it('imports an exported starter and rejects malformed, overlapping and oversized layouts', () => {
    const layout = laptopProject(); expect(importProject(JSON.stringify(layout))).toEqual(layout);
    for (const text of ['bad', '{}', 'x'.repeat(262145), JSON.stringify({ ...layout, schemaVersion: 99 })]) expect(() => importProject(text)).toThrow();
    expect(() => validateProject({ ...layout, name: '' })).toThrow();
    expect(() => importProject(JSON.stringify({ ...layout, widgets: [...layout.widgets, layout.widgets[0]] }))).toThrow();
    expect(() => importProject(JSON.stringify({ ...layout, widgets: [{ ...layout.widgets[0], type: 'future' }] }))).toThrow();
    expect(() => importProject(JSON.stringify({ ...layout, widgets: [{ ...layout.widgets[0], step: -1 }] }))).toThrow();
    expect(() => importProject(JSON.stringify({ ...layout, widgets: [{ ...layout.widgets[0], action: 'shell;exit' }] }))).toThrow();
  });

  it('imports valid JSON, retains invalid input for correction and creates a starter', () => {
    const replace = vi.fn(() => true), close = vi.fn(); render(<ProjectDialog layout={createEmptyLayout()} onReplace={replace} onClose={close} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '{}' } }); fireEvent.click(screen.getByRole('button', { name: 'Import layout', exact: true }));
    expect(screen.getByRole('alert')).toBeTruthy(); expect(close).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: JSON.stringify(laptopProject()) } }); fireEvent.click(screen.getByRole('button', { name: 'Import layout', exact: true }));
    expect(replace).toHaveBeenCalledWith(laptopProject()); expect(close).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Laptop controls starter' })); expect(replace).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: 'New empty project' })); expect(replace).toHaveBeenLastCalledWith(createEmptyLayout());
  });

  it('exports only layout JSON and revokes the temporary download URL', () => {
    vi.useFakeTimers(); const create = vi.fn(() => 'blob:layout'), revoke = vi.fn();
    URL.createObjectURL = create; URL.revokeObjectURL = revoke; const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<ProjectDialog layout={laptopProject()} onReplace={() => true} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Export layout' })); expect(create.mock.calls[0][0]).toBeInstanceOf(Blob); expect(click).toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1000)); expect(revoke).toHaveBeenCalledWith('blob:layout');
  });

  it('renders the supplied Janus logo and renames the project without accepting blank names', () => {
    const name = vi.fn(), action = vi.fn(); render(<TopBar name="Desk" onName={name} onProject={action} connected={false} status="Connect backend" busy={false} onConnect={action} onPair={action} onSave={action} onHistory={action} />);
    expect(screen.getByRole('img', { name: 'Janus' }).getAttribute('src')).toBe('./janus-logo.svg');
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: '  Work  ' } }); fireEvent.blur(screen.getByLabelText('Project name')); expect(name).toHaveBeenCalledWith('Work');
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: '  ' } }); fireEvent.blur(screen.getByLabelText('Project name')); expect((screen.getByLabelText('Project name') as HTMLInputElement).value).toBe('Desk');
    expect(screen.getByRole('button', { name: 'Pair Device' }).hasAttribute('disabled')).toBe(true);
  });
});

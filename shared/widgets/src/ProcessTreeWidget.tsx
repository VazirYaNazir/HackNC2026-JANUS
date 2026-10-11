import { Frame } from './Frame';
import WidgetTitle from './WidgetTitle';
import { processRows } from './processTree';
import type { WidgetProps } from './types';

export default function ProcessTreeWidget(props: WidgetProps) {
  const rows = processRows(props.value);
  return <Frame props={props} selectable>
    <WidgetTitle widget={props.widget} />
    <div className="jw__processes">
      {rows === null ? <p>{props.telemetryStatus && props.telemetryStatus !== 'Live' ? props.telemetryStatus : 'Waiting for processes…'}</p> : rows.length === 0 ? <p>No apps exceed the CPU or RAM thresholds.</p> : <table aria-label={props.widget.label}>
        <thead><tr><th>App / PID</th><th>CPU</th><th>RAM</th></tr></thead>
        <tbody>{rows.map(p => <tr key={p.pid} className={p.context ? 'jw__process-context' : undefined}>
          <td style={{ paddingLeft: `${p.depth * 10 + 4}px` }}>{p.name}<small> {p.pid}</small></td>
          <td>{p.cpu_percent === null ? '—' : `${p.cpu_percent.toFixed(1)}%`}</td>
          <td>{p.ram_bytes === null ? '—' : `${Math.round(p.ram_bytes / 1048576)} MB`}</td>
        </tr>)}</tbody>
      </table>}
    </div>
  </Frame>;
}

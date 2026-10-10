export function Sidebar({ side }: { side: 'left' | 'right' }) {
  return <aside className={`sidebar glass-surface sidebar-${side}`} aria-label={`${side} sidebar`} />
}

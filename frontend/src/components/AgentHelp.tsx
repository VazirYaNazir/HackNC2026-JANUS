import { useEffect, useState } from 'react'
import { agentHelpView } from '../agentHelp'

const COMMAND = 'npm start -- --only=agent'
const WAIT_MS = 45_000
const POLL_MS = 5_000

/** Shown while the backend is reachable but no laptop agent is connected. Only Electron can spawn the agent. */
export function AgentHelp() {
  const desktop = window.janusDesktop
  const [running, setRunning] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState('')

  // Is an agent process already up (from npm start or an earlier click) even though it has not connected yet?
  useEffect(() => {
    if (!desktop) return
    let cancelled = false
    const poll = () => { void desktop.agentStatus().then((status) => { if (!cancelled) setRunning(status.running) }).catch(() => undefined) }
    poll()
    const timer = window.setInterval(poll, POLL_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [desktop])

  useEffect(() => {
    if (!waiting) return
    const timer = window.setTimeout(() => setWaiting(false), WAIT_MS)
    return () => window.clearTimeout(timer)
  }, [waiting])

  const start = async () => {
    setError(''); setWaiting(true)
    try { await desktop?.startAgent() } catch (reason) { setWaiting(false); setError(reason instanceof Error ? reason.message : 'Could not start the agent.') }
  }

  const view = agentHelpView({ desktop: desktop !== undefined, running, waiting })
  if (view === 'copy-command') {
    return <div className="studio-notice" role="status">No laptop agent is connected. Run <code>{COMMAND}</code> in the JANUS folder.<button type="button" onClick={() => void navigator.clipboard?.writeText(COMMAND)}>Copy command</button></div>
  }
  if (view === 'waiting') return <div className="studio-notice" role="status">An agent is starting or already running. Waiting for it to connect…</div>
  return <div className="studio-notice" role="status">No laptop agent is connected.<button type="button" onClick={() => void start()}>Start agent</button>{error && <span role="alert">{error}</span>}</div>
}

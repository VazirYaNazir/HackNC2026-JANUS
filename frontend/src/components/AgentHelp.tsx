import { useState } from 'react'

const COMMAND = 'npm start -- --only=agent'

/** Shown while the backend is reachable but no laptop agent is connected. Only Electron can spawn the agent. */
export function AgentHelp() {
  const desktop = window.janusDesktop
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const start = async () => {
    setStarting(true); setError('')
    try { await desktop?.startAgent() } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start the agent.') }
    finally { setStarting(false) }
  }
  if (desktop) {
    return <div className="studio-notice" role="status">No laptop agent is connected.<button type="button" onClick={() => void start()} disabled={starting}>{starting ? 'Starting…' : 'Start agent'}</button>{error && <span role="alert">{error}</span>}</div>
  }
  return <div className="studio-notice" role="status">No laptop agent is connected. Run <code>{COMMAND}</code> in the Janus folder.<button type="button" onClick={() => void navigator.clipboard?.writeText(COMMAND)}>Copy command</button></div>
}

import { useState, type FormEvent } from 'react'
import { PanelClient } from '@janus/client'

export function ConnectionDialog({ onConnect, onClose }: { onConnect: (client: PanelClient) => Promise<void>; onClose: () => void }) {
  const [host, setHost] = useState(import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000')
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const connect = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const client = new PanelClient({ host: host.trim(), token: token.trim() })
      await client.request('/api/v1/studio/session')
      await onConnect(client); onClose()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not connect.') }
    finally { setBusy(false) }
  }
  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="connect-heading"><h2 id="connect-heading">Connect to your backend</h2>
    <form onSubmit={(event) => void connect(event)}><label>Backend address<input value={host} onChange={(event) => setHost(event.target.value)} autoFocus required /></label>
      <label>Laptop controller credential<input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" required /></label>
      <p>Use the controller credential registered for this laptop. It stays in this window’s memory.</p>
      {error && <p className="janus-error" role="alert">{error}</p>}
      <div className="janus-actions"><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button></div>
    </form></section></div>
}

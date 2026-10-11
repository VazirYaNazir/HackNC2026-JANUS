import { useState, type FormEvent } from 'react'
import { PanelClient } from '@janus/client'

export function ConnectionDialog({ onConnect, onClose }: { onConnect: (client: PanelClient) => Promise<void>; onClose: () => void }) {
  const [host, setHost] = useState(import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000')
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const open = async (address: string, credential: string) => {
    setBusy(true); setError('')
    try {
      const client = new PanelClient({ host: address.trim(), token: credential.trim() })
      await client.request('/api/v1/studio/session')
      await onConnect(client); onClose()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not connect.') }
    finally { setBusy(false) }
  }
  const connect = (event: FormEvent) => { event.preventDefault(); return open(host, token) }
  // Electron only: the main process reads this laptop's credential and hands it over IPC, so nothing is pasted.
  const connectLocal = async () => {
    const local = await window.janusDesktop?.getLocalConnection().catch(() => null)
    if (!local) { setError('No local backend credentials found. Run npm run setup, or paste the credential below.'); return }
    await open(local.host, local.token)
  }
  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="connect-heading"><h2 id="connect-heading">Connect to your backend</h2>
    {window.janusDesktop && <p><button type="button" onClick={() => void connectLocal()} disabled={busy}>Use this laptop’s local backend</button></p>}
    <form onSubmit={(event) => void connect(event)}><label>Backend address<input value={host} onChange={(event) => setHost(event.target.value)} autoFocus required /></label>
      <label>Laptop controller credential<input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" required /></label>
      <p>Use the controller credential registered for this laptop. It stays in this window’s memory.</p>
      {error && <p className="janus-error" role="alert">{error}</p>}
      <div className="janus-actions"><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button></div>
    </form></section></div>
}

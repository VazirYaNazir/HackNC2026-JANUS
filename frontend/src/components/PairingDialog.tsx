import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { backendHost, pairingLink, pairingTarget, PanelClient, type PairingCode } from '@janus/client'

export function PairingDialog({ client, onClose }: { client: PanelClient; onClose: () => void }) {
  const [pairing, setPairing] = useState<(PairingCode & { expires: number }) | null>(null)
  const [host, setHost] = useState(client.host)
  const [image, setImage] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const [error, setError] = useState('')
  const [generation, setGeneration] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    const requested = Date.now()
    void client.pairing(controller.signal).then((value) => {
      if (!controller.signal.aborted) {
        const target = pairingTarget(value.host || client.host, value.code)
        setHost(target.host)
        setPairing({ ...value, code: target.code, expires: requested + value.expires_in_seconds * 1000 })
        setNow(Date.now())
      }
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not create a pairing code.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [client, generation])

  let link = '', payload = '', addressError = '', loopback = false
  try {
    const address = backendHost(host.trim())
    loopback = ['localhost', '127.0.0.1', '[::1]'].includes(new URL(address).hostname)
    if (pairing) {
      const target = pairingTarget(address, pairing.code)
      link = pairingLink(target, pairing.pwa_url || undefined)
      payload = pairing.pwa_url ? link : JSON.stringify(target)
    }
  } catch (reason) { addressError = reason instanceof Error ? reason.message : 'Enter a valid HTTPS backend address.' }
  const expired = pairing !== null && pairing.expires <= now

  useEffect(() => {
    let current = true
    if (payload && !expired) void QRCode.toDataURL(payload, { width: 256, margin: 2 })
      .then((next) => { if (current) setImage(next) })
      .catch(() => { if (current) setError('Could not draw the QR code. Use the address and code below.') })
    return () => { current = false }
  }, [payload, expired])

  const refresh = () => {
    setPairing(null); setImage(''); setError(''); setLoading(true)
    setGeneration((value) => value + 1)
  }

  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="pair-heading">
    <h2 id="pair-heading">Pair your phone</h2>
    <p>Open the JANUS phone app, choose <strong>Scan to add panel</strong>, and scan this QR. You can also choose <strong>Enter code instead</strong> and enter the address and code below.</p>
    {pairing?.pwa_url && <p>Scanning with your phone’s Camera app opens JANUS with the pairing details filled in. Tap Pair to connect.</p>}
    <label>Phone backend address<input value={host} onChange={(event) => { setImage(''); setHost(event.target.value) }} inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} /></label>
    {loopback && <p className="janus-error" role="alert">Your phone needs an HTTPS backend address. Enter the URL printed by your backend tunnel.</p>}
    {addressError && <p className="janus-error" role="alert">{addressError}</p>}
    {!loading && !expired && payload && image && <img src={image} width="256" height="256" alt="JANUS pairing QR code" />}
    {pairing && !expired && <><p>Pairing code: <strong>{pairing.code}</strong></p>
      {link && <label>Pairing link<input readOnly value={link} onFocus={(event) => event.target.select()} /></label>}
    </>}
    <p role="status">{loading ? 'Creating code…' : expired ? 'Code expired. Create a new code in Studio.' : pairing ? `Expires in ${Math.ceil((pairing.expires - now) / 1000)} seconds. Each code can be used once.` : 'Could not create a pairing code.'}</p>
    {error && <p className="janus-error" role="alert">{error}</p>}
    <div className="janus-actions"><button type="button" onClick={onClose}>Close</button><button type="button" disabled={loading} onClick={refresh}>New code</button></div>
  </section></div>
}

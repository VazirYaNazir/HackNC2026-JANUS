import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { PanelClient } from '@janus/client'

export function PairingDialog({ client, onClose }: { client: PanelClient; onClose: () => void }) {
  const [image, setImage] = useState('')
  const [code, setCode] = useState('')
  const [expires, setExpires] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [error, setError] = useState('')
  const [generation, setGeneration] = useState(0)
  useEffect(() => {
    let current = true
    void client.pairing().then(async (value) => {
      const image = await QRCode.toDataURL(JSON.stringify({ host: client.host, code: value.code }), { width: 256, margin: 2 })
      if (current) { setCode(value.code); setImage(image); setExpires(Date.now() + value.expires_in_seconds * 1000) }
    }).catch((reason) => { if (current) setError(reason instanceof Error ? reason.message : 'Could not create a pairing code.') })
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => { current = false; clearInterval(timer) }
  }, [client, generation])
  return <div className="janus-dialog-backdrop"><section className="janus-request" role="dialog" aria-modal="true" aria-labelledby="pair-heading"><h2 id="pair-heading">Pair your phone</h2>
    <p>Open Janus on your phone and scan this code.</p>{image && <img src={image} width="256" height="256" alt="Janus pairing QR code" />}
    <p>Address: {client.host}</p><p>Code: <strong>{code}</strong></p><p>{expires > now ? `Expires in ${Math.ceil((expires - now) / 1000)} seconds.` : code ? 'Code expired. Create a new code.' : 'Creating code…'}</p>
    {error && <p className="janus-error" role="alert">{error}</p>}
    <div className="janus-actions"><button type="button" onClick={onClose}>Close</button><button type="button" onClick={() => setGeneration((value) => value + 1)}>New code</button></div>
  </section></div>
}

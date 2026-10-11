// Thin Photon Spectrum sidecar: iMessage text in -> backend -> backend's reply out.
// No business logic here (see backend/app/messaging). Credentials come from the environment and are never logged.
import dns from 'node:dns'

// First thing, before spectrum-ts (loaded dynamically below) opens a single connection: some networks have a broken
// IPv6 path to Photon, and Node would otherwise try it first. start.mjs also sets NODE_OPTIONS=--dns-result-order=ipv4first.
dns.setDefaultResultOrder('ipv4first')

import { fileURLToPath } from 'node:url'
import { assertLoopback, forward, heartbeat, type BackendConfig, type Fetch } from './backend.js'
import { GreetingStore, greet, parseAllowlist } from './greeting.js'
import { ProcessedStore, SenderQueues, handleMessage, inboundLine, mask } from './pipeline.js'
import { connectWithRetry, describeError, isNetworkError } from './retry.js'

// Missing credentials are the only reason to exit at startup.
function required(name: string): string {
  const value = process.env[name]
  if (!value) {
    console.error(`[imessage] ${name} is not set. Add it to backend/.env (npm run setup creates MESSAGING_SERVICE_TOKEN).`)
    process.exit(1)
  }
  return value
}

const projectId = required('SPECTRUM_PROJECT_ID')
const projectSecret = required('SPECTRUM_PROJECT_SECRET')
const config: BackendConfig = { url: process.env.JANUS_BACKEND_URL ?? 'http://127.0.0.1:8000', token: required('MESSAGING_SERVICE_TOKEN') }
assertLoopback(config.url)

// A dropped socket deep inside the SDK must not take the sidecar down; anything that is not a network problem still does,
// loudly, so a real bug is never hidden behind a retry loop.
for (const event of ['unhandledRejection', 'uncaughtException'] as const) {
  process.on(event, (error: unknown) => {
    if (isNetworkError(error)) {
      console.error(`[imessage] network error ignored (${describeError(error)})`)
      return
    }
    console.error(`[imessage] unexpected error, stopping (${describeError(error)})`)
    process.exit(1)
  })
}

const { Spectrum } = await import('spectrum-ts')
const { imessage } = await import('spectrum-ts/providers/imessage')

const send = fetch as unknown as Fetch
const stateDir = process.env.JANUS_STATE_DIR ?? fileURLToPath(new URL('../../.janus/', import.meta.url))
const processed = new ProcessedStore(stateDir)

const createApp = () => Spectrum({ projectId, projectSecret, providers: [imessage.config()] })
type SpectrumApp = Awaited<ReturnType<typeof createApp>>

// Studio shows "iMessage: connected" only while this reports in, and only while Photon is actually connected.
let connected = false
const beat = () => { if (connected) heartbeat(send, config).catch(() => undefined) }
const reportDown = () => heartbeat(send, config, false).catch(() => undefined)
const timer = setInterval(beat, 15_000)

let current: SpectrumApp | null = null
let stopping = false
const stop = async () => {
  if (stopping) return
  stopping = true
  clearInterval(timer)
  connected = false
  await Promise.race([reportDown(), new Promise((resolve) => setTimeout(resolve, 1_500))])
  // A stuck connection must not keep the process alive.
  if (current) await Promise.race([current.stop(), new Promise((resolve) => setTimeout(resolve, 5_000))]).catch(() => undefined)
  process.exit(0)
}
process.on('SIGINT', () => void stop())
process.on('SIGTERM', () => void stop())

// The loop below only hands messages to per-sender queues; it never waits on a send, so one slow or hung message
// cannot stall the stream or any other sender.
const queues = new SenderQueues({
  onError: (key, error) => console.error(`[imessage] ${mask(key)} handler failed: ${describeError(error)}`),
})

let greeted = false
while (!stopping) {
  const app = await connectWithRetry({ connect: createApp, stopped: () => stopping })
  if (!app) break
  current = app
  connected = true
  console.log('[imessage] connected to Photon Spectrum; waiting for messages')
  beat()

  // Once per day per allowlisted number, say hello. Runs alongside the message loop and never blocks or crashes it.
  if (!greeted && process.env.IMESSAGE_GREETING !== '0') {
    greeted = true
    const im = imessage(app)
    void greet({
      phones: parseAllowlist(process.env.IMESSAGE_ALLOWED_SENDERS),
      store: new GreetingStore(stateDir),
      openDm: async (phone) => {
        const user = await im.user(phone)
        const dm = await im.space.create(user)
        return { line: imessage(dm).phone, send: (text) => dm.send(text) }
      },
    })
  }

  try {
    for await (const [space, message] of app.messages) {
      if (message.direction !== 'inbound') continue
      // Logged before any filtering, so nothing is dropped silently.
      console.log(inboundLine(message.sender?.id, message.content.type, message.id))
      if (message.content.type !== 'text') { console.log(`[imessage] ignoring a non-text message (type ${message.content.type}) #${message.id.slice(0, 8)}`); continue }
      const sender = message.sender?.id
      if (!sender) { console.log(`[imessage] ignoring a message with no sender #${message.id.slice(0, 8)}`); continue }
      const text = message.content.text
      const messageId = message.id
      const accepted = queues.enqueue(sender, () => handleMessage({
        sender,
        text,
        messageId,
        processed,
        forward: () => forward(send, config, { sender, text, messageId }),
        sendText: (reply) => space.send(reply),
        typing: (work) => space.responding(work),
      }))
      if (!accepted) console.error(`[imessage] ${mask(sender)} #${messageId.slice(0, 8)} dropped: too many messages waiting`)
    }
    console.log('[imessage] the Photon message stream ended; reconnecting')
  } catch (error) {
    console.error(`[imessage] lost the Photon connection (${describeError(error)}); reconnecting`)
  }
  connected = false
  void reportDown()
  await Promise.race([app.stop(), new Promise((resolve) => setTimeout(resolve, 3_000))]).catch(() => undefined)
  current = null
}

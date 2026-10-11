// Thin Photon Spectrum sidecar: iMessage text in -> backend -> backend's reply out.
// No business logic here (see backend/app/messaging). Credentials come from the environment and are never logged.
import { Spectrum } from 'spectrum-ts'
import { imessage } from 'spectrum-ts/providers/imessage'
import { fileURLToPath } from 'node:url'
import { assertLoopback, forward, heartbeat, type BackendConfig, type Fetch } from './backend.js'
import { GreetingStore, greet, parseAllowlist } from './greeting.js'
import { ProcessedStore, SenderQueues, handleMessage, inboundLine, mask } from './pipeline.js'

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

const send = fetch as unknown as Fetch
const stateDir = process.env.JANUS_STATE_DIR ?? fileURLToPath(new URL('../../.janus/', import.meta.url))
const processed = new ProcessedStore(stateDir)

const app = await Spectrum({ projectId, projectSecret, providers: [imessage.config()] })
console.log('[imessage] connected to Photon Spectrum; waiting for messages')

// Once per day per allowlisted number, say hello. Runs alongside the message loop and never blocks or crashes it.
if (process.env.IMESSAGE_GREETING !== '0') {
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

// Lets Studio show "iMessage: connected". Failures only matter while the backend is down.
const beat = () => heartbeat(send, config).catch(() => undefined)
void beat()
const timer = setInterval(() => void beat(), 15_000)

let stopping = false
const stop = async () => {
  if (stopping) return
  stopping = true
  clearInterval(timer)
  // A stuck connection must not keep the process alive.
  await Promise.race([app.stop(), new Promise((resolve) => setTimeout(resolve, 5_000))]).catch(() => undefined)
  process.exit(0)
}
process.on('SIGINT', () => void stop())
process.on('SIGTERM', () => void stop())

// The loop below only hands messages to per-sender queues; it never waits on a send, so one slow or hung message
// cannot stall the stream or any other sender.
const queues = new SenderQueues({
  onError: (key, error) => console.error(`[imessage] ${mask(key)} handler failed: ${error instanceof Error ? error.message : 'unknown error'}`),
})

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

import { useState, type FormEvent } from 'react'
import { Icon } from '@janus/design'
import type { GenerationRequest } from '@janus/schema'
import type { Session } from '@janus/client'

const capabilities = ['files.read', 'files.write', 'network', 'external.send', 'desktop.mouse', 'desktop.screen', 'audio.volume', 'process.inspect', 'process.spawn', 'admin', 'persistent.write']
const dependencies = { Darwin: ['coreaudio', 'applicationservices', 'iokit', 'imageio', 'mac-system'], Windows: ['win32'] }

export function BottomBar({ enabled, target, onGenerate }: { enabled: boolean; target: Session['target']; onGenerate: (request: GenerationRequest) => Promise<void> }) {
  const [description, setDescription] = useState('')
  const [operation, setOperation] = useState('compute')
  const [platform, setPlatform] = useState<GenerationRequest['platform']>(target?.platform ?? 'Darwin')
  const [architecture, setArchitecture] = useState<GenerationRequest['architecture']>(target?.architecture ?? 'arm64')
  const [permissions, setPermissions] = useState<string[]>([])
  const [libraries, setLibraries] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true)
    try { await onGenerate({ description: description.trim(), operation, platform: target?.platform ?? platform, architecture: target?.architecture ?? architecture, capabilities: permissions, dependencies: libraries.filter((name) => dependencies[target?.platform ?? platform].includes(name)), output_protocol: 'json-v1' }) }
    finally { setBusy(false) }
  }
  return <form className="bottom-bar studio-glass" aria-label="AI assistant toolbar" onSubmit={(event) => void submit(event)}>
    <div className="assistant-label"><Icon name="sparkle" size={16} /><span>Assistant</span></div>
    <label className="assistant-prompt"><span className="sr-only">AI assistant prompt</span><input value={description} maxLength={4096} onChange={(event) => setDescription(event.target.value)} disabled={!enabled || busy} placeholder="Describe what the selected control should do…" required /></label>
    <details className="assistant-options"><summary>Execution settings</summary><div>
      <label>Operation<input value={operation} pattern="[a-z][a-z0-9_.-]{0,63}" required onChange={(event) => setOperation(event.target.value)} /></label>
      {!target && <><label>Platform<select value={platform} onChange={(event) => setPlatform(event.target.value as GenerationRequest['platform'])}><option>Darwin</option><option>Windows</option></select></label><label>Architecture<select value={architecture} onChange={(event) => setArchitecture(event.target.value as GenerationRequest['architecture'])}><option>arm64</option><option>x86_64</option><option>x86</option></select></label></>}
      <fieldset><legend>Requested capabilities</legend>{capabilities.map((name) => <label key={name}><input type="checkbox" checked={permissions.includes(name)} onChange={(event) => setPermissions((items) => event.target.checked ? [...items, name] : items.filter((item) => item !== name))} />{name}</label>)}</fieldset>
      <fieldset><legend>Platform libraries</legend>{dependencies[target?.platform ?? platform].map((name) => <label key={name}><input type="checkbox" checked={libraries.includes(name)} onChange={(event) => setLibraries((items) => event.target.checked ? [...items, name] : items.filter((item) => item !== name))} />{name}</label>)}</fieldset>
      <p>These settings request access. The laptop’s local grant decides what is allowed.</p>
    </div></details>
    <button className="studio-button studio-button-primary" type="submit" disabled={!enabled || busy || !description.trim()}><Icon name="sparkle" size={15} /><span>{busy ? 'Preparing…' : 'Generate'}</span></button>
  </form>
}

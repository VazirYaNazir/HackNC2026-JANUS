import { useId, useRef, useState, type FormEvent } from 'react'
import { Icon } from '@janus/design'
import type { GenerationRequest } from '@janus/schema'
import { generationTarget, type Session } from '@janus/client'

const capabilities = ['files.read', 'files.write', 'network', 'external.send', 'desktop.mouse', 'desktop.screen', 'audio.volume', 'process.inspect', 'process.spawn', 'admin', 'persistent.write']
const dependencies = { Darwin: ['coreaudio', 'applicationservices', 'iokit', 'imageio', 'mac-system'], Windows: ['win32'], Linux: [] as string[] }

export function BottomBar({ enabled, target, online = true, hint, disabledReason, sourceOnly = false, onGenerate }: { enabled: boolean; target: Session['target']; online?: boolean; hint?: string; disabledReason?: string; sourceOnly?: boolean; onGenerate: (request: GenerationRequest, autoAccess?: boolean, allowedCapabilities?: string[]) => Promise<void> }) {
  const worker = target?.execution_target
  const actualTarget = generationTarget({ device_id: '', online: true, target })
  const canPlan = online && !!worker && target?.build_boundary_available === true && target.execution_boundary_available === true
  const [autoAccess, setAutoAccess] = useState(true)
  const [excluded, setExcluded] = useState<string[]>([])
  const automatic = autoAccess && canPlan
  const supported = worker?.capabilities ?? capabilities
  const [description, setDescription] = useState('')
  const [operation, setOperation] = useState('compute')
  const [platform, setPlatform] = useState<GenerationRequest['platform']>(actualTarget?.platform ?? 'Darwin')
  const [architecture, setArchitecture] = useState<GenerationRequest['architecture']>(actualTarget?.architecture ?? 'arm64')
  const [permissions, setPermissions] = useState<string[]>([])
  const [libraries, setLibraries] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const hintId = useId()
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!enabled || submitting.current || !description.trim() || (!automatic && !/^[a-z][a-z0-9_.-]{0,63}$/.test(operation))) return
    submitting.current = true; setBusy(true)
    try { await onGenerate({ description: description.trim(), operation, platform: actualTarget?.platform ?? platform, architecture: actualTarget?.architecture ?? architecture, capabilities: permissions, dependencies: libraries.filter((name) => dependencies[actualTarget?.platform ?? platform].includes(name)), output_protocol: 'json-v1' }, automatic, supported.filter(name => !excluded.includes(name))) }
    finally { submitting.current = false; setBusy(false) }
  }
  return <form className="bottom-bar studio-glass" aria-label="AI assistant toolbar" onSubmit={(event) => void submit(event)}>
    <div className="assistant-label"><Icon name="sparkle" size={16} /><span>Assistant</span></div>
    <div className="assistant-prompt"><label className="sr-only" htmlFor={`${hintId}-prompt`}>AI assistant prompt</label><input id={`${hintId}-prompt`} value={description} maxLength={4096} onChange={(event) => setDescription(event.target.value)} disabled={!enabled || busy} aria-describedby={hintId} placeholder="Describe the C program you want to generate…" required /><small className="assistant-hint" id={hintId}>{!enabled ? disabledReason ?? 'Connect your backend to generate C source.' : hint ?? 'Generate C source to review and download.'}</small></div>
    <details className="assistant-options"><summary>Generation settings</summary><fieldset className="assistant-settings" disabled={!enabled || busy}>
      <label><input type="checkbox" checked={autoAccess} disabled={!canPlan} onChange={(event) => setAutoAccess(event.target.checked)} />Choose minimum access automatically</label>
      {!canPlan && <p>Automatic access planning becomes available when the isolated worker is ready. Set the requested access manually below.</p>}
      {!automatic && <label>Operation<input value={operation} pattern="[a-z][a-z0-9_.-]{0,63}" required onChange={(event) => setOperation(event.target.value)} /></label>}
      {!target && <><label>Platform<select value={platform} onChange={(event) => setPlatform(event.target.value as GenerationRequest['platform'])}><option>Darwin</option><option>Windows</option><option>Linux</option></select></label><label>Architecture<select value={architecture} onChange={(event) => setArchitecture(event.target.value as GenerationRequest['architecture'])}><option>arm64</option><option>x86_64</option><option>x86</option></select></label></>}
      <fieldset><legend>{automatic ? 'Maximum allowed access' : 'Requested capabilities'}</legend>{capabilities.map((name) => <label key={name}><input type="checkbox" disabled={!supported.includes(name)} checked={automatic ? supported.includes(name) && !excluded.includes(name) : permissions.includes(name)} onChange={(event) => automatic ? setExcluded((items) => event.target.checked ? items.filter((item) => item !== name) : [...items, name]) : setPermissions((items) => event.target.checked ? [...items, name] : items.filter((item) => item !== name))} />{name}</label>)}</fieldset>
      <fieldset disabled={automatic}><legend>Platform libraries</legend>{dependencies[actualTarget?.platform ?? platform].map((name) => <label key={name}><input type="checkbox" checked={libraries.includes(name)} onChange={(event) => setLibraries((items) => event.target.checked ? [...items, name] : items.filter((item) => item !== name))} />{name}</label>)}</fieldset>
      {worker && <p>Portable C runs in the isolated Linux worker. File and process access stays inside that worker. Supported native controls use the validated laptop broker.</p>}
      <p>{automatic ? 'The planner requests only the access needed for your prompt, within the maximum allowed access. ' : ''}These settings request access. The laptop’s local grant decides what is allowed.</p>
    </fieldset></details>
    <button className="studio-button studio-button-primary" type="submit" disabled={!enabled || busy || !description.trim() || (!automatic && !/^[a-z][a-z0-9_.-]{0,63}$/.test(operation))}><Icon name="sparkle" size={15} /><span>{busy ? sourceOnly ? 'Generating…' : 'Preparing…' : 'Generate'}</span></button>
  </form>
}

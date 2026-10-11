import { Icon } from '@janus/design'

export function BottomBar() {
  return <footer className="bottom-bar studio-glass" aria-label="AI assistant toolbar">
    <div className="assistant-label"><Icon name="sparkle" size={16} /><span>Assistant</span></div>
    <label className="assistant-prompt"><span className="sr-only">AI assistant prompt</span><input disabled placeholder="Describe a dashboard change…" /></label>
    <button className="studio-button studio-button-primary" type="button" disabled title="AI generation is not implemented"><Icon name="sparkle" size={15} /><span>Generate</span></button>
  </footer>
}

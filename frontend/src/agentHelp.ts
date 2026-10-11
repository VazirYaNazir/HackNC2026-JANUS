export type AgentHelpView = 'copy-command' | 'start' | 'waiting'

/**
 * What the "no agent connected" notice offers. The Start button is only for Electron, and only while no agent
 * process exists or is starting: a second agent for the same laptop is refused by the backend and just adds a process.
 */
export function agentHelpView({ desktop, running, waiting }: { desktop: boolean; running: boolean; waiting: boolean }): AgentHelpView {
  if (!desktop) return 'copy-command'
  return running || waiting ? 'waiting' : 'start'
}

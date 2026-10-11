/** Bridge exposed by Electron's preload script. Absent in a plain browser. */
interface JanusDesktop {
  getLocalConnection(): Promise<{ host: string; token: string; deviceId: string } | null>
  agentStatus(): Promise<{ running: boolean }>
  startAgent(): Promise<{ running: boolean }>
}

interface Window {
  janusDesktop?: JanusDesktop
}

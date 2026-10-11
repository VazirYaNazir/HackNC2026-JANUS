import { pairBackend, PanelClient } from "@janus/client";
import { isDemoHost } from "./demo";

export { parsePairingLink } from "@janus/client";

/** What the PC returns after a successful pairing. */
export interface PairResult {
  token: string;
  pcName: string;
  agentDeviceId: string;
}

/**
 * Exchange a single-use laptop-bound code for a scoped phone credential.
 */
export async function pair(host: string, code: string, signal?: AbortSignal): Promise<PairResult> {
  return pairBackend(host, code, 'Mobile phone', signal);
}

/** True when this phone's authenticated laptop session is online. */
export async function checkHost(host: string, token: string, timeoutMs = 3000): Promise<boolean> {
  if (isDemoHost(host)) return true; // the Demo panel is always "online" and never touches the network
  try {
    return (await new PanelClient({ host, token }).session(AbortSignal.timeout(timeoutMs))).online;
  } catch {
    return false;
  }
}

import { useSyncExternalStore } from "react";

/** Chrome's install prompt event (not in the DOM lib types). */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export type InstallKind = "prompt" | "ios" | null;

const DISMISS_KEY = "janus.installDismissed";

export const isStandalone = (): boolean =>
  window.matchMedia("(display-mode: standalone)").matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true;

/** iPhone, iPad (including iPadOS, which reports itself as a Mac), or iPod. */
export const isIos = (): boolean =>
  /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

export function isDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissInstall(): void {
  try {
    localStorage.setItem(DISMISS_KEY, "1");
  } catch {
    // storage unavailable; it will show again next visit
  }
}

// Chrome fires beforeinstallprompt once, early; keep it here so a banner mounted later can still use it.
let deferred: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferred = e as BeforeInstallPromptEvent;
  emit();
});
window.addEventListener("appinstalled", () => {
  deferred = null;
  emit();
});

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const hasPrompt = () => deferred !== null;

/** How this browser can install the app right now: a native prompt (Android/Chrome), iOS instructions, or not at all. */
export function useInstallKind(): InstallKind {
  const prompt = useSyncExternalStore(subscribe, hasPrompt);
  if (isStandalone()) return null;
  if (prompt) return "prompt";
  return isIos() ? "ios" : null;
}

/** Shows Chrome's install dialog. The event can only be used once. */
export async function promptInstall(): Promise<void> {
  const event = deferred;
  if (!event) return;
  deferred = null;
  emit();
  await event.prompt();
  await event.userChoice;
}

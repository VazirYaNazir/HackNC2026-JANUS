import { useEffect, useRef, useState, type FormEvent } from "react";
import { Html5Qrcode } from "html5-qrcode";
import { Icon } from "@janus/design";
import type { PairQR, SavedPanel } from "@janus/schema";
import { pairingTarget } from "@janus/client";
import { pair, parsePairingLink, type PairResult } from "../lib/connection";
import { addPanel, renamePanel, repairPanel } from "../lib/panels";
import "./ScanScreen.css";

interface ScanScreenProps {
  /** Leave the scanner. `panel` is the re-paired panel; it is missing if the user closed the screen. */
  onDone: (panel?: SavedPanel) => void;
  /** Set when re-pairing a saved panel: it keeps its name and id, and only its connection is replaced. */
  repairing?: SavedPanel;
  initialTarget?: PairQR;
  method?: "scan" | "manual";
}

type Phase = "scan" | "manual" | "link" | "pairing" | "name";
type Entry = "scan" | "manual" | "link";

const REGION_ID = "scan-region";

export default function ScanScreen({ onDone, repairing, initialTarget, method = "scan" }: ScanScreenProps) {
  const [phase, setPhase] = useState<Phase>(initialTarget ? "manual" : method);
  const [error, setError] = useState("");
  const [link, setLink] = useState("");
  const [host, setHost] = useState(initialTarget?.host ?? repairing?.host ?? "");
  const [code, setCode] = useState(initialTarget?.code ?? "");
  const [name, setName] = useState("");
  const [frameSize, setFrameSize] = useState<number>();
  const [paired, setPaired] = useState<(PairResult & { panel: SavedPanel }) | null>(null);
  const busy = useRef(false); // a QR can decode many times a second; pair only once
  const request = useRef<AbortController | null>(null);
  const lastScan = useRef("");

  useEffect(() => () => request.current?.abort(), []);

  const startPairing = async (target: PairQR, entry: Entry) => {
    if (busy.current) return;
    busy.current = true;
    const controller = new AbortController();
    request.current = controller;
    let completed = false;
    setError("");
    setHost(target.host);
    setCode(target.code);
    setPhase("pairing");
    try {
      const result = await pair(target.host, target.code, controller.signal);
      if (controller.signal.aborted) return;
      if (repairing) {
        // A panel's layout and history belong to one PC. Never repoint it at a different one.
        if (repairing.agentDeviceId && result.agentDeviceId !== repairing.agentDeviceId) {
          setError(`That code is for a different PC (${result.pcName}). Scan the code from the PC this panel belongs to, or add that PC as a new panel from the home screen.`);
          setPhase(entry);
          return;
        }
        const updated = repairPanel(repairing.id, { host: target.host, token: result.token, pcName: result.pcName, agentDeviceId: result.agentDeviceId });
        completed = true;
        onDone(updated.find((p) => p.id === repairing.id));
        return;
      }
      // The code is now consumed. Save its credential before asking for a name,
      // so closing or reloading this screen cannot lose a successful connection.
      const panels = addPanel({ name: result.pcName, pcName: result.pcName, host: target.host, token: result.token, agentDeviceId: result.agentDeviceId });
      setPaired({ ...result, panel: panels[panels.length - 1] });
      setName(result.pcName);
      completed = true;
      setPhase("name");
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Couldn't pair with that PC. Check the code and try again.");
        setPhase(entry);
      }
    } finally {
      if (!completed) busy.current = false;
    }
  };

  // The camera callback outlives renders; it calls the latest startPairing through this ref.
  const startPairingRef = useRef(startPairing);
  useEffect(() => {
    startPairingRef.current = startPairing;
  });

  // Camera: runs only while the scan phase is showing.
  useEffect(() => {
    if (phase !== "scan") return;
    const scanner = new Html5Qrcode(REGION_ID, false);
    let cancelled = false;
    let started: Promise<unknown> | undefined;
    // Start on the next tick: React StrictMode mounts, unmounts and remounts at once in dev, and this
    // way the first (discarded) mount never opens the camera.
    const timer = window.setTimeout(() => {
      started = scanner
        .start(
          { facingMode: "environment" },
          {
            fps: 10,
            qrbox: (w, h) => {
              const side = Math.floor(Math.min(w, h) * 0.7);
              if (!cancelled) setFrameSize(side);
              return { width: side, height: side };
            },
          },
          (text) => {
            if (cancelled) return;
            const parsed = parsePairingLink(text);
            if (parsed) {
              const key = JSON.stringify(parsed);
              if (key === lastScan.current) return;
              lastScan.current = key;
              void startPairingRef.current(parsed, "scan");
            }
            else setError("That isn't a JANUS QR code.");
          },
          undefined,
        )
        .catch(() => {
          if (!cancelled) setError("Camera unavailable. Allow camera access on an HTTPS page, or enter the code instead.");
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      // If start() is still pending, let it settle before stopping so the stream isn't leaked.
      void started
        ?.then(() => (scanner.isScanning ? scanner.stop() : undefined))
        .then(() => scanner.clear())
        .catch(() => undefined);
    };
  }, [phase]);

  const submitManual = (e: FormEvent) => {
    e.preventDefault();
    try { void startPairing(pairingTarget(host, code), "manual"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Enter the backend address and complete pairing code from Studio."); }
  };

  const submitLink = (e: FormEvent) => {
    e.preventDefault();
    const target = parsePairingLink(link);
    if (target) void startPairing(target, "link");
    else setError("That isn't a valid pairing link. Paste the whole link shown on your PC.");
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!paired || !name.trim()) return;
    renamePanel(paired.panel.id, name.trim());
    onDone();
  };

  return (
    <main className="scan">
      <button type="button" className="btn btn--icon scan__close" aria-label="Close" disabled={phase === "pairing"} onClick={() => onDone()}>
        <Icon name="close" size={22} />
      </button>

      {phase === "scan" && (
        <>
          <div className="scan__video">
            <div id={REGION_ID} />
          </div>
          <div className="scan__frame" aria-hidden="true" style={frameSize ? { width: frameSize, height: frameSize } : undefined} />
          <div className="scan__bottom">
            <p className={error ? "error" : "scan__hint"} role={error ? "alert" : undefined}>
              {error || (repairing ? `Scan the QR code on your PC to reconnect “${repairing.name}”.` : "Point your camera at the QR code on your PC.")}
            </p>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setError("");
                setPhase("manual");
              }}
            >
              Enter code instead
            </button>
            <button type="button" className="btn" onClick={() => { setError(""); setPhase("link"); }}>Paste pairing link</button>
          </div>
        </>
      )}

      {phase === "manual" && (
        <form className="scan__panel" onSubmit={submitManual}>
          <h1>{repairing ? "Pair again" : "Enter code"}</h1>
          <p className="scan__hint">In desktop Studio, choose Pair Device. Enter its phone backend address and one-time code.</p>
          <label className="field">
            <span>PC address</span>
            <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="https://abc.trycloudflare.com" inputMode="url" autoCapitalize="none" autoComplete="off" autoCorrect="off" spellCheck={false} autoFocus />
          </label>
          <label className="field">
            <span>Pairing code</span>
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="A1B2C3D4E5F6" autoCapitalize="characters" autoComplete="off" autoCorrect="off" spellCheck={false} />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" className="btn btn--amber" disabled={!host.trim() || !code.trim()}>Pair</button>
          <button type="button" className="btn" onClick={() => { setError(""); lastScan.current = ""; setPhase("scan"); }}>Scan instead</button>
          <button type="button" className="btn" onClick={() => { setError(""); setPhase("link"); }}>Paste pairing link</button>
        </form>
      )}

      {phase === "link" && (
        <form className="scan__panel" onSubmit={submitLink}>
          <h1>Enter link</h1>
          <label className="field">
            <span>Pairing link</span>
            <input
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="https://abc.trycloudflare.com?pairing_code=A1B2C3D4E5F6"
              inputMode="url"
              autoCapitalize="none"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              autoFocus
            />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" className="btn btn--amber" disabled={!link.trim()}>
            Pair
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setError("");
              lastScan.current = "";
              setPhase("scan");
            }}
          >
            Scan instead
          </button>
          <button type="button" className="btn" onClick={() => { setError(""); setPhase("manual"); }}>Enter code instead</button>
        </form>
      )}

      {phase === "pairing" && (
        <div className="scan__panel" role="status">
          <h1>Pairing…</h1>
          <p className="scan__hint">Connecting to your PC.</p>
        </div>
      )}

      {phase === "name" && (
        <form className="scan__panel" onSubmit={save}>
          <h1>Paired with {paired?.pcName}</h1>
          <label className="field">
            <span>Panel name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} autoFocus />
          </label>
          <button type="submit" className="btn btn--amber" disabled={!name.trim()}>
            Save
          </button>
        </form>
      )}
    </main>
  );
}

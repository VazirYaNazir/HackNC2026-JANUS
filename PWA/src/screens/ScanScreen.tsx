import { useEffect, useRef, useState, type FormEvent } from "react";
import { Html5Qrcode } from "html5-qrcode";
import { Icon } from "@janus/design";
import type { PairQR } from "@janus/schema";
import { pair, type PairResult } from "../lib/connection";
import { addPanel } from "../lib/panels";
import "./ScanScreen.css";

interface ScanScreenProps {
  /** Back to Home, with or without a new panel. */
  onDone: () => void;
}

type Phase = "scan" | "manual" | "pairing" | "name";

const REGION_ID = "scan-region";

/** The QR encodes { host, code } as JSON. Returns null for anything else. */
function parseQr(text: string): PairQR | null {
  try {
    const data: unknown = JSON.parse(text);
    if (typeof data !== "object" || data === null) return null;
    const { host, code } = data as Record<string, unknown>;
    if (typeof host !== "string" || typeof code !== "string" || !code) return null;
    const url = new URL(host);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return { host: url.origin, code };
  } catch {
    return null;
  }
}

export default function ScanScreen({ onDone }: ScanScreenProps) {
  const [phase, setPhase] = useState<Phase>("scan");
  const [error, setError] = useState("");
  const [host, setHost] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [paired, setPaired] = useState<(PairResult & { host: string }) | null>(null);
  const busy = useRef(false); // a QR can decode many times a second; pair only once

  const startPairing = async (target: PairQR) => {
    if (busy.current) return;
    busy.current = true;
    setError("");
    setPhase("pairing");
    try {
      const result = await pair(target.host, target.code);
      setPaired({ ...result, host: target.host });
      setName(result.pcName);
      setPhase("name");
    } catch {
      setError("Couldn't pair with that PC. Check the code and try again.");
      setPhase("scan");
    } finally {
      busy.current = false;
    }
  };

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
              return { width: side, height: side };
            },
          },
          (text) => {
            const parsed = parseQr(text);
            if (parsed) void startPairing(parsed);
            else setError("That isn't a Janus QR code.");
          },
          undefined,
        )
        .catch(() => {
          if (!cancelled) setError("Camera unavailable. Allow camera access, or enter the code instead.");
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
    const target = parseQr(JSON.stringify({ host: host.trim(), code: code.trim() }));
    if (target) void startPairing(target);
    else setError("Enter the address shown on your PC, starting with https://");
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!paired || !name.trim()) return;
    addPanel({ name: name.trim(), pcName: paired.pcName, host: paired.host, token: paired.token, agentDeviceId: paired.agentDeviceId });
    onDone();
  };

  return (
    <main className="scan">
      <button type="button" className="btn btn--icon scan__close" aria-label="Close" onClick={onDone}>
        <Icon name="close" size={22} />
      </button>

      {phase === "scan" && (
        <>
          <div className="scan__video">
            <div id={REGION_ID} />
          </div>
          <div className="scan__frame" aria-hidden="true" />
          <div className="scan__bottom">
            <p className={error ? "error" : "scan__hint"} role={error ? "alert" : undefined}>
              {error || "Point your camera at the QR code on your PC."}
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
          </div>
        </>
      )}

      {phase === "manual" && (
        <form className="scan__panel" onSubmit={submitManual}>
          <h1>Enter code</h1>
          <label className="field">
            <span>PC address</span>
            <input
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="https://abc.trycloudflare.com"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              autoFocus
            />
          </label>
          <label className="field">
            <span>Pairing code</span>
            <input value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="none" autoCorrect="off" />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" className="btn btn--amber" disabled={!host.trim() || !code.trim()}>
            Pair
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setError("");
              setPhase("scan");
            }}
          >
            Scan instead
          </button>
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

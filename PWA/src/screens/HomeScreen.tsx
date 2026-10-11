import { useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "@janus/design";
import type { SavedPanel } from "@janus/schema";
import Dialog from "../components/Dialog";
import InstallBanner from "../components/InstallBanner";
import { checkHost } from "../lib/connection";
import { isDemoPanel } from "../lib/demo";
import { addDemoPanel, listPanels, removePanel, renamePanel } from "../lib/panels";
import "./HomeScreen.css";

interface HomeScreenProps {
  onOpen: (panel: SavedPanel) => void;
  onScan: () => void;
}

type Sheet = { kind: "menu" | "rename" | "remove"; panel: SavedPanel } | null;
type Status = "checking" | "online" | "offline";

const LONG_PRESS_MS = 500;

export default function HomeScreen({ onOpen, onScan }: HomeScreenProps) {
  const [panels, setPanels] = useState(listPanels);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [name, setName] = useState("");
  const [status, setStatus] = useState<Record<string, Status>>({});
  const pressTimer = useRef<number>(undefined);
  const longPressed = useRef(false);

  // Ping each saved PC so its dot shows whether it is reachable.
  const hosts = JSON.stringify(panels.map(({ id, host, token }) => ({ id, host, token })));
  useEffect(() => {
    let cancelled = false;
    for (const { id, host, token } of JSON.parse(hosts) as Pick<SavedPanel, "id" | "host" | "token">[]) {
      void checkHost(host, token).then((ok) => {
        if (!cancelled) setStatus((prev) => ({ ...prev, [id]: ok ? "online" : "offline" }));
      });
    }
    return () => {
      cancelled = true;
    };
  }, [hosts]);

  const startPress = (panel: SavedPanel) => {
    longPressed.current = false;
    pressTimer.current = window.setTimeout(() => {
      longPressed.current = true;
      setSheet({ kind: "menu", panel });
    }, LONG_PRESS_MS);
  };
  const endPress = () => window.clearTimeout(pressTimer.current);

  const close = () => setSheet(null);
  const submitRename = (e: FormEvent) => {
    e.preventDefault();
    const next = name.trim();
    if (sheet && next) setPanels(renamePanel(sheet.panel.id, next));
    close();
  };

  return (
    <main className="home">
      <h1 className="home__title">Janus</h1>

      {panels.length === 0 ? (
        <>
          <InstallBanner prominent />
          <p className="home__empty">Open Janus on your PC and scan its QR code to add your first panel.</p>
          <p className="home__demo">
            <button type="button" className="home__link" onClick={() => setPanels(addDemoPanel())}>
              Add demo panel
            </button>
          </p>
        </>
      ) : (
        <>
          <InstallBanner />
          <ul className="home__list">
            {panels.map((panel) => {
              const state = status[panel.id] ?? "checking";
              return (
                <li key={panel.id} className="card">
                  <button
                    type="button"
                    className="card__main"
                    onClick={() => {
                      // A long press already opened the menu; swallow the click that follows it.
                      if (longPressed.current) longPressed.current = false;
                      else onOpen(panel);
                    }}
                    onPointerDown={() => startPress(panel)}
                    onPointerUp={endPress}
                    onPointerLeave={endPress}
                    onPointerCancel={endPress}
                    onContextMenu={(e) => e.preventDefault()}
                  >
                    <span className={`card__dot card__dot--${state}`} role="img" aria-label={state} />
                    <span className="card__text">
                      <span className="card__title">
                        <span className="card__name">{panel.name}</span>
                        {isDemoPanel(panel) && <span className="card__badge">Demo</span>}
                      </span>
                      <span className="card__pc">{panel.pcName}</span>
                    </span>
                    <Icon name="chevron" size={20} className="card__chevron" />
                  </button>
                  <button
                    type="button"
                    className="card__more"
                    aria-label={`Options for ${panel.name}`}
                    onClick={() => setSheet({ kind: "menu", panel })}
                  >
                    <Icon name="more" size={20} />
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <div className="home__bar">
        <button type="button" className="btn btn--amber home__scan" onClick={onScan}>
          <Icon name="scan" size={20} />
          Scan to add panel
        </button>
      </div>

      {sheet?.kind === "menu" && (
        <Dialog title={sheet.panel.name} onClose={close}>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setName(sheet.panel.name);
              setSheet({ kind: "rename", panel: sheet.panel });
            }}
          >
            Rename
          </button>
          <button type="button" className="btn btn--coral" onClick={() => setSheet({ kind: "remove", panel: sheet.panel })}>
            Remove
          </button>
          <button type="button" className="btn" onClick={close}>
            Cancel
          </button>
        </Dialog>
      )}
      {sheet?.kind === "rename" && (
        <Dialog title="Rename panel" onClose={close}>
          <form className="dialog__form" onSubmit={submitRename}>
            <label className="field">
              <span>Name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={40} />
            </label>
            <div className="dialog__row">
              <button type="button" className="btn" onClick={close}>
                Cancel
              </button>
              <button type="submit" className="btn btn--amber" disabled={!name.trim()}>
                Save
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {sheet?.kind === "remove" && (
        <Dialog title="Remove panel?" onClose={close}>
          <p className="dialog__text">
            “{sheet.panel.name}” will be removed from this phone. You can add it again by scanning its QR code.
          </p>
          <div className="dialog__row">
            <button type="button" className="btn" onClick={close}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn--coral"
              onClick={() => {
                setPanels(removePanel(sheet.panel.id));
                close();
              }}
            >
              Remove
            </button>
          </div>
        </Dialog>
      )}
    </main>
  );
}

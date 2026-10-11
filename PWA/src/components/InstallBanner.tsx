import { useState } from "react";
import { Icon } from "@janus/design";
import { dismissInstall, isDismissed, promptInstall, useInstallKind } from "../lib/install";
import "./InstallBanner.css";

interface InstallBannerProps {
  /** Large version for the empty state. It ignores an earlier dismissal and has no close button. */
  prominent?: boolean;
}

/** Offers to install Janus. Hidden once installed (standalone) or where the browser can't install. */
export default function InstallBanner({ prominent = false }: InstallBannerProps) {
  const kind = useInstallKind();
  const [dismissed, setDismissed] = useState(isDismissed);

  if (!kind || (dismissed && !prominent)) return null;

  return (
    <section className={`install ${prominent ? "install--prominent" : ""}`} aria-label="Install Janus">
      <div className="install__body">
        <h2 className="install__title">Install Janus</h2>
        {kind === "ios" ? (
          <p className="install__text">
            Tap <Icon name="share" size={18} className="install__share" aria-label="Share" /> Share, then{" "}
            <strong>Add to Home Screen</strong>.
          </p>
        ) : (
          <p className="install__text">Add Janus to your home screen to open it like an app.</p>
        )}
        {prominent && (
          <p className="install__note">
            Install before adding panels: the installed app keeps its own saved panels, separate from Safari.
          </p>
        )}
      </div>
      {kind === "prompt" && (
        <button type="button" className="btn btn--amber install__action" onClick={() => void promptInstall()}>
          <Icon name="download" size={18} />
          Install Janus
        </button>
      )}
      {!prominent && (
        <button
          type="button"
          className="install__close"
          aria-label="Dismiss install tip"
          onClick={() => {
            dismissInstall();
            setDismissed(true);
          }}
        >
          <Icon name="close" size={18} />
        </button>
      )}
    </section>
  );
}

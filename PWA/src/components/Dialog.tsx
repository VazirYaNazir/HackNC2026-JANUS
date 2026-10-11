import { useEffect, type ReactNode } from "react";
import "./Dialog.css";

interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/** Bottom sheet over a dimmed backdrop. Closes on Escape or a tap outside. */
export default function Dialog({ title, onClose, children }: DialogProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="dialog" onClick={onClose}>
      <div className="dialog__sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2 className="dialog__title">{title}</h2>
        {children}
      </div>
    </div>
  );
}

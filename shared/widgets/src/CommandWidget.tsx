import { Frame } from "./Frame";
import PromptForm from "./PromptForm";
import type { WidgetProps } from "./types";

export default function CommandWidget(props: WidgetProps) {
  const { widget, state, error } = props;
  const status = state === "running" ? "Running…" : state === "error" ? error ?? "Request failed. Check recent requests for the reason." : state === "done" ? "Completed" : state;
  return (
    <Frame props={props} selectable>
      <PromptForm props={props} placeholder={widget.label} icon={widget.icon} />
      {status && <p className={`jw__reply ${state === "error" ? "jw__reply--error" : ""}`} aria-live="polite" tabIndex={0}>{status}</p>}
    </Frame>
  );
}

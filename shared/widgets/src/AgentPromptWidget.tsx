import { Frame } from "./Frame";
import PromptForm from "./PromptForm";
import type { WidgetProps } from "./types";

const RUN_STATES = ["running", "done", "error"];

export default function AgentPromptWidget(props: WidgetProps) {
  const { widget, state } = props;
  // `state` is a run state while the agent works, or the reply text once it has answered.
  const reply = state !== undefined && !RUN_STATES.includes(state) ? state : undefined;
  const status = state === "running" ? "Thinking…" : state === "error" ? "Something went wrong." : reply;

  return (
    <Frame props={props} selectable className={status ? "jw--reply" : ""}>
      <PromptForm props={props} placeholder="Ask Gemini…" icon={widget.icon ?? "sparkle"} />
      {status && (
        <p
          key={status} // a new answer starts at the top
          className={`jw__reply ${state === "error" ? "jw__reply--error" : ""}`}
          aria-live="polite"
          tabIndex={0}
        >
          {status}
        </p>
      )}
    </Frame>
  );
}

import { Frame } from "./Frame";
import PromptForm from "./PromptForm";
import type { WidgetProps } from "./types";

export default function CommandWidget(props: WidgetProps) {
  const { widget } = props;
  return (
    <Frame props={props} selectable>
      <PromptForm props={props} placeholder={widget.label} icon={widget.icon} />
    </Frame>
  );
}

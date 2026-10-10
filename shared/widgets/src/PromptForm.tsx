import { useRef, useState, type FormEvent } from "react";
import { Icon } from "@janus/design";
import { activate, type WidgetProps } from "./types";

interface PromptFormProps {
  props: WidgetProps;
  placeholder: string;
  icon?: string;
}

/** Inset text field + raised amber send key. Enter or tap sends the text, then clears the field. */
export default function PromptForm({ props, placeholder, icon }: PromptFormProps) {
  const { widget, mode } = props;
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const edit = mode === "edit";
  const empty = text.trim() === "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (edit || empty) return;
    activate(props, text.trim());
    setText("");
    inputRef.current?.focus();
  };

  return (
    <form className="jw__send" onSubmit={submit}>
      <label className="jw__field">
        {icon && <Icon name={icon} className="jw__ico" />}
        <input
          ref={inputRef}
          type="text"
          value={text}
          placeholder={placeholder}
          aria-label={widget.label}
          disabled={edit}
          enterKeyHint="send"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <button type="submit" className="jw__key" aria-label={`Send ${widget.label}`} disabled={edit || empty}>
        <Icon name="send" size={18} />
      </button>
    </form>
  );
}

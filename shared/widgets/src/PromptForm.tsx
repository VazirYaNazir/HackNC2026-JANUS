import { useRef, useState, type FormEvent } from "react";
import { Icon } from "@janus/design";
import { activate, type WidgetProps } from "./types";

interface PromptFormProps {
  props: WidgetProps;
  placeholder: string;
  icon?: string;
}

/** Inset text field + raised amber send key. Enter or tap sends the text, preserves the text until the request is accepted. */
export default function PromptForm({ props, placeholder, icon }: PromptFormProps) {
  const { widget, mode } = props;
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const edit = mode === "edit";
  const empty = text.trim() === "";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (edit || empty || submitting) return;
    const submitted = text;
    setSubmitting(true);
    try {
      const accepted = await activate(props, text.trim());
      if (accepted === true) setText(current => current === submitted ? "" : current);
    } finally { setSubmitting(false); inputRef.current?.focus(); }
  };

  return (
    <form className="jw__send" onSubmit={(event) => void submit(event)}>
      <label className="jw__field">
        {icon && <Icon name={icon} className="jw__ico" />}
        <input
          ref={inputRef}
          type="text"
          maxLength={4096}
          value={text}
          placeholder={placeholder}
          aria-label={widget.label}
          disabled={edit || submitting}
          enterKeyHint="send"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <button type="submit" className="jw__key" aria-label={`Send ${widget.label}`} disabled={edit || empty || submitting}>
        <Icon name="send" size={18} />
      </button>
    </form>
  );
}

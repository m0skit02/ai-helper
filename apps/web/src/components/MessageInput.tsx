import { useState, type FormEvent, type KeyboardEvent } from "react";

type Props = {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function MessageInput({
  onSend,
  disabled = false,
  placeholder = "Сообщение…",
}: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    const t = value.trim();
    if (!t || disabled) return;
    onSend(t);
    setValue("");
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="messageInput" onSubmit={onSubmit}>
      <textarea
        className="messageInput-field"
        rows={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        aria-label="Текст сообщения"
      />
      <button
        type="submit"
        className="btn btn-primary messageInput-send"
        disabled={disabled || !value.trim()}
      >
        Отправить
      </button>
    </form>
  );
}

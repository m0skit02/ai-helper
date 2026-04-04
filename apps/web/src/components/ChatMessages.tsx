import { useEffect, useRef } from "react";
import type { MessageItem, TaskResponse } from "../api";
import { TaskCard } from "./TaskCard";

type Props = {
  messages: MessageItem[];
  tasksById: Record<string, TaskResponse | undefined>;
  sending: boolean;
  onAfterConfirm?: () => void | Promise<void>;
  onTaskError?: (message: string) => void;
};

export function ChatMessages({
  messages,
  tasksById,
  sending,
  onAfterConfirm,
  onTaskError,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  return (
    <div className="chatMessages" role="log" aria-live="polite">
      {messages.length === 0 && !sending ? (
        <div className="emptyState chatMessages-empty">
          <p>Нет сообщений в этом чате.</p>
          <p className="emptyState-sub">Напишите запрос ниже.</p>
        </div>
      ) : null}

      <ul className="chatMessages-list">
        {messages.map((m) => (
          <li key={m.message_id} className="chatMessages-row">
            <MessageBubble message={m} />
            {m.role === "assistant" && m.task_id ? (
              <div className="chatMessages-task">
                {tasksById[m.task_id] ? (
                  <TaskCard
                    task={tasksById[m.task_id]!}
                    onAfterConfirm={onAfterConfirm}
                    onError={onTaskError}
                  />
                ) : (
                  <div className="taskCard taskCard-loading">Загрузка задачи…</div>
                )}
              </div>
            ) : null}
          </li>
        ))}
      </ul>

      {sending ? (
        <div className="chatMessages-typing" aria-busy="true">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </div>
      ) : null}
      <div ref={bottomRef} />
    </div>
  );
}

function MessageBubble({ message }: { message: MessageItem }) {
  const isUser = message.role === "user";
  const isSystem =
    message.role === "system" || message.role === "tool";

  if (isSystem) {
    return (
      <div className="messageBubble messageBubble-system">
        <span className="messageBubble-label">{message.role}</span>
        <p className="messageBubble-text">{message.content}</p>
      </div>
    );
  }

  return (
    <div
      className={`messageBubble ${isUser ? "messageBubble-user" : "messageBubble-assistant"}`}
    >
      <p className="messageBubble-text">{message.content}</p>
    </div>
  );
}

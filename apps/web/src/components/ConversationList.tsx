import type { ConversationResponse } from "../api";

type Props = {
  items: ConversationResponse[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  loading?: boolean;
  sidebarOpen: boolean;
  onCloseMobile?: () => void;
};

export function ConversationList({
  items,
  selectedId,
  onSelect,
  onNewChat,
  loading = false,
  sidebarOpen,
  onCloseMobile,
}: Props) {
  return (
    <aside
      className={`conversationList ${sidebarOpen ? "conversationList-open" : ""}`}
      aria-label="Диалоги"
    >
      <div className="conversationList-header">
        <h2 className="conversationList-title">Чаты</h2>
        <button
          type="button"
          className="btn btn-primary conversationList-new"
          onClick={onNewChat}
          disabled={loading}
        >
          Новый чат
        </button>
      </div>

      {loading && items.length === 0 ? (
        <p className="emptyState">Загрузка…</p>
      ) : null}

      {!loading && items.length === 0 ? (
        <p className="emptyState">Нет диалогов. Создайте новый чат.</p>
      ) : null}

      <ul className="conversationList-items">
        {items.map((c) => (
          <li key={c.conversation_id}>
            <button
              type="button"
              className={`conversationList-item ${
                c.conversation_id === selectedId ? "conversationList-item-active" : ""
              }`}
              onClick={() => {
                onSelect(c.conversation_id);
                onCloseMobile?.();
              }}
            >
              <span className="conversationList-itemTitle">{c.title}</span>
              <time
                className="conversationList-itemTime"
                dateTime={c.updated_at}
              >
                {formatShortDate(c.updated_at)}
              </time>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function formatShortDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
    });
  } catch {
    return "";
  }
}

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createConversation,
  getTask,
  listConversations,
  listMessages,
  sendMessage,
  getHealth,
} from "./api";
import type { ConversationResponse, MessageItem, TaskResponse } from "./api";
import { ChatMessages } from "./components/ChatMessages";
import { ConversationList } from "./components/ConversationList";
import { MessageInput } from "./components/MessageInput";
import { usePollActiveTasks } from "./hooks/usePollActiveTasks";
import "./chat.css";

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationResponse[]>([]);
  const [convLoading, setConvLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  const [tasksById, setTasksById] = useState<
    Record<string, TaskResponse | undefined>
  >({});

  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<{ kind: "error" | "info"; text: string } | null>(
    null,
  );
  const [healthOk, setHealthOk] = useState<boolean | null>(null);

  const [sidebarOpen, setSidebarOpen] = useState(false);

  const showError = useCallback((text: string) => {
    setToast({ kind: "error", text });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const refreshConversations = useCallback(async () => {
    try {
      const { items } = await listConversations();
      setConversations(items);
    } catch (e) {
      showError(
        e instanceof ApiError
          ? `Список чатов: ${e.status}`
          : "Не удалось загрузить чаты",
      );
    }
  }, [showError]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setConvLoading(true);
      await refreshConversations();
      if (!cancelled) setConvLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshConversations]);

  useEffect(() => {
    getHealth()
      .then(() => setHealthOk(true))
      .catch(() => setHealthOk(false));
  }, []);

  const loadMessages = useCallback(
    async (conversationId: string) => {
      setMessagesLoading(true);
      setTasksById({});
      try {
        const { items } = await listMessages(conversationId);
        setMessages(items);
      } catch (e) {
        setMessages([]);
        showError(
          e instanceof ApiError
            ? `Сообщения: ${e.status}`
            : "Не удалось загрузить сообщения",
        );
      } finally {
        setMessagesLoading(false);
      }
    },
    [showError],
  );

  useEffect(() => {
    if (!selectedId) {
      setMessages([]);
      setTasksById({});
      return;
    }
    loadMessages(selectedId);
  }, [selectedId, loadMessages]);

  useEffect(() => {
    if (!selectedId || messages.length === 0) return;
    let cancelled = false;
    const taskIds = [
      ...new Set(
        messages
          .filter((m) => m.role === "assistant" && m.task_id)
          .map((m) => m.task_id as string),
      ),
    ];
    taskIds.forEach((taskId) => {
      getTask(taskId)
        .then((t) => {
          if (!cancelled) {
            setTasksById((prev) => ({ ...prev, [taskId]: t }));
          }
        })
        .catch(() => {
          /* 404 / network — остаётся placeholder в UI */
        });
    });
    return () => {
      cancelled = true;
    };
  }, [messages, selectedId]);

  const mergeTask = useCallback((taskId: string, task: TaskResponse) => {
    setTasksById((prev) => ({ ...prev, [taskId]: task }));
  }, []);

  usePollActiveTasks(selectedId, tasksById, mergeTask);

  const handleNewChat = async () => {
    try {
      const conv = await createConversation();
      setConversations((prev) => [conv, ...prev]);
      setSelectedId(conv.conversation_id);
      setSidebarOpen(false);
    } catch (e) {
      showError(
        e instanceof ApiError ? `Новый чат: ${e.status}` : "Не создать чат",
      );
    }
  };

  const handleSend = async (text: string) => {
    let cid = selectedId;
    if (!cid) {
      try {
        const conv = await createConversation();
        setConversations((prev) => [conv, ...prev]);
        cid = conv.conversation_id;
        setSelectedId(cid);
      } catch (e) {
        showError(
          e instanceof ApiError ? `Чат: ${e.status}` : "Не создать чат",
        );
        return;
      }
    }

    setSending(true);
    try {
      const created = await sendMessage(cid, {
        content: text,
        allow_social_actions: true,
      });
      setTasksById((prev) => ({
        ...prev,
        [created.task.task_id]: created.task,
      }));
      const { items } = await listMessages(cid);
      setMessages(items);
      await refreshConversations();
    } catch (e) {
      showError(
        e instanceof ApiError ? `Отправка: ${e.status}` : "Не отправить сообщение",
      );
    } finally {
      setSending(false);
    }
  };

  const handleAfterConfirm = async () => {
    if (!selectedId) return;
    try {
      const { items } = await listMessages(selectedId);
      setMessages(items);
      const taskIds = [
        ...new Set(
          items
            .filter((m) => m.role === "assistant" && m.task_id)
            .map((m) => m.task_id as string),
        ),
      ];
      await Promise.all(
        taskIds.map(async (id) => {
          try {
            const t = await getTask(id);
            setTasksById((prev) => ({ ...prev, [id]: t }));
          } catch {
            /* ignore */
          }
        }),
      );
      await refreshConversations();
    } catch (e) {
      showError(
        e instanceof ApiError ? `Обновление: ${e.status}` : "Не обновить данные",
      );
    }
  };

  const canType = !sending && !convLoading;

  return (
    <div className="chatApp">
      {toast ? (
        <div
          className={`chatToast chatToast-${toast.kind}`}
          role="status"
        >
          {toast.text}
          <button
            type="button"
            className="chatToast-close"
            aria-label="Закрыть"
            onClick={() => setToast(null)}
          >
            ×
          </button>
        </div>
      ) : null}

      <header className="chatHeader">
        <button
          type="button"
          className="chatHeader-menu"
          aria-label="Открыть список чатов"
          onClick={() => setSidebarOpen(true)}
        >
          ☰
        </button>
        <div className="chatHeader-brand">
          <h1 className="chatHeader-title">AI Helper</h1>
          {healthOk === false ? (
            <span className="chatHeader-health chatHeader-health-bad">API offline</span>
          ) : healthOk === true ? (
            <span className="chatHeader-health chatHeader-health-ok" title="Backend OK">
              ●
            </span>
          ) : null}
        </div>
        {selectedId ? (
          <span className="chatHeader-meta">
            {conversations.find((c) => c.conversation_id === selectedId)?.title ??
              "Чат"}
          </span>
        ) : (
          <span className="chatHeader-meta muted">Выберите или создайте чат</span>
        )}
      </header>

      <div className="chatBody">
        {sidebarOpen ? (
          <button
            type="button"
            className="chatBackdrop"
            aria-label="Закрыть меню"
            onClick={() => setSidebarOpen(false)}
          />
        ) : null}

        <ConversationList
          items={conversations}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onNewChat={handleNewChat}
          loading={convLoading}
          sidebarOpen={sidebarOpen}
          onCloseMobile={() => setSidebarOpen(false)}
        />

        <main className="chatMain">
          {messagesLoading ? (
            <p className="inlineStatus">Загрузка сообщений…</p>
          ) : null}
          <ChatMessages
            messages={messages}
            tasksById={tasksById}
            sending={sending}
            onAfterConfirm={handleAfterConfirm}
            onTaskError={showError}
          />
        </main>
      </div>

      <footer className="chatFooter">
        <MessageInput
          onSend={handleSend}
          disabled={!canType}
          placeholder={
            selectedId
              ? "Сообщение…"
              : "Сообщение… (будет создан новый чат)"
          }
        />
      </footer>
    </div>
  );
}

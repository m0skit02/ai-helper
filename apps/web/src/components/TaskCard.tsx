import { useState } from "react";
import type { ActionItem, TaskResponse } from "../api";
import { confirmAction } from "../api";
import { TracePanel } from "./TracePanel";

type Props = {
  task: TaskResponse;
  onAfterConfirm?: () => void | Promise<void>;
  onError?: (message: string) => void;
};

function str(v: unknown): string | undefined {
  if (v === undefined || v === null) return undefined;
  const s = String(v).trim();
  return s.length ? s : undefined;
}

export function TaskCard({
  task,
  onAfterConfirm,
  onError,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const result = task.result;
  const product = result?.product;
  const title = product ? str(product.title) : undefined;
  const price = product ? str(product.price) : undefined;
  const currency = product ? str(product.currency) : undefined;
  const url = product ? str(product.url) : undefined;

  const news = Array.isArray(result?.news) ? result!.news : [];
  const sources = Array.isArray(result?.sources) ? result!.sources : [];
  const actions = Array.isArray(result?.actions) ? result!.actions : [];

  const handleConfirm = async (action: ActionItem, decision: "approve" | "reject") => {
    setConfirming(true);
    try {
      await confirmAction({
        task_id: task.task_id,
        action_id: action.action_id,
        decision,
      });
      await onAfterConfirm?.();
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "Confirm failed");
    } finally {
      setConfirming(false);
    }
  };

  const buttonsLocked = confirming;

  return (
    <div className="taskCard">
      <div className="taskCard-header">
        <span className={`taskStatus taskStatus-${task.status}`}>{task.status}</span>
        {task.error ? (
          <span className="taskCard-error" title={task.error}>
            {task.error}
          </span>
        ) : null}
      </div>

      {title || price || url ? (
        <div className="taskCard-product">
          <div className="taskCard-product-title">
            {title ?? "Товар"}
            {price ? (
              <span className="taskCard-price">
                {price}
                {currency ? ` ${currency}` : ""}
              </span>
            ) : null}
          </div>
          {url ? (
            <a
              className="taskCard-link"
              href={url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Открыть ссылку
            </a>
          ) : null}
        </div>
      ) : null}

      {news.length > 0 ? (
        <div className="taskCard-section">
          <h4 className="taskCard-sectionTitle">Новости</h4>
          <ul className="taskCard-news">
            {news.map((item, i) => (
              <li key={i} className="taskCard-newsItem">
                <span className="taskCard-newsTitle">
                  {str(item.title) ?? "Без заголовка"}
                </span>
                {str(item.summary) ? (
                  <p className="taskCard-newsSummary">{str(item.summary)}</p>
                ) : null}
                {str(item.url) ? (
                  <a
                    className="taskCard-link"
                    href={str(item.url)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Источник
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {sources.filter(Boolean).length > 0 ? (
        <div className="taskCard-section">
          <h4 className="taskCard-sectionTitle">Источники</h4>
          <ul className="taskCard-sources">
            {sources.map((s, i) =>
              s ? (
                <li key={i}>
                  <a
                    className="taskCard-link"
                    href={s}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {truncate(s, 64)}
                  </a>
                </li>
              ) : null,
            )}
          </ul>
        </div>
      ) : null}

      {actions.length > 0 ? (
        <div className="taskCard-section">
          <h4 className="taskCard-sectionTitle">Действия</h4>
          <ul className="taskCard-actions">
            {actions.map((a) => (
              <li key={a.action_id} className="taskCard-actionRow">
                <span className="taskCard-actionType">{a.type}</span>
                <span className={`taskStatus taskStatus-${a.status}`}>{a.status}</span>
                {a.status === "waiting_confirm" ? (
                  <div className="taskCard-confirm">
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={buttonsLocked}
                      onClick={() => handleConfirm(a, "approve")}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost"
                      disabled={buttonsLocked}
                      onClick={() => handleConfirm(a, "reject")}
                    >
                      Reject
                    </button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <TracePanel trace={task.trace} />
    </div>
  );
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

import { useId, useState } from "react";
import type { TraceItem } from "../api";

type Props = {
  trace: TraceItem[];
};

export function TracePanel({ trace }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);

  if (!trace.length) {
    return null;
  }

  return (
    <div className="tracePanel">
      <button
        type="button"
        className="tracePanel-toggle"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        Trace ({trace.length}) {open ? "▼" : "▶"}
      </button>
      {open && (
        <ul id={id} className="tracePanel-list">
          {trace.map((item, i) => (
            <li key={`${item.step}-${item.ts}-${i}`} className="tracePanel-row">
              <span className="tracePanel-step">{item.step}</span>
              <span className="tracePanel-meta">
                {item.status}
                {item.tool ? ` · ${item.tool}` : ""}
              </span>
              <time className="tracePanel-time" dateTime={item.ts}>
                {formatTs(item.ts)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatTs(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

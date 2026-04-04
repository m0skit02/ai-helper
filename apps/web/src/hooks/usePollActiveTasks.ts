import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { getTask } from "../api";
import type { TaskResponse, TaskStatus } from "../api";

const POLL_MS = 2500;

const ACTIVE: TaskStatus[] = ["running", "needs_confirmation"];

function needsPoll(task: TaskResponse | undefined): boolean {
  return !!task && ACTIVE.includes(task.status);
}

function pollKeyFrom(
  tasksById: Record<string, TaskResponse | undefined>,
): string {
  return Object.entries(tasksById)
    .filter(([, t]) => needsPoll(t))
    .map(([id, t]) => `${id}:${t!.status}`)
    .sort()
    .join("|");
}

/**
 * Polls GET /task/{id} every ~2.5s while any tracked task is running or needs_confirmation.
 * Restarts when the set of active tasks changes; stops when none are active or on unmount.
 */
export function usePollActiveTasks(
  conversationId: string | null,
  tasksById: Record<string, TaskResponse | undefined>,
  onUpdate: (taskId: string, task: TaskResponse) => void,
): void {
  const tasksRef = useRef(tasksById);
  const onUpdateRef = useRef(onUpdate);

  useLayoutEffect(() => {
    tasksRef.current = tasksById;
    onUpdateRef.current = onUpdate;
  });

  const pollKey = useMemo(() => pollKeyFrom(tasksById), [tasksById]);

  useEffect(() => {
    if (!conversationId || pollKey === "") {
      return;
    }

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    const tick = async () => {
      if (cancelled) return;
      const map = tasksRef.current;
      const activeIds = Object.entries(map)
        .filter(([, t]) => needsPoll(t))
        .map(([id]) => id);

      if (activeIds.length === 0) {
        return;
      }

      for (const taskId of activeIds) {
        if (!needsPoll(tasksRef.current[taskId])) continue;
        try {
          const fresh = await getTask(taskId);
          if (cancelled) return;
          onUpdateRef.current(taskId, fresh);
        } catch {
          /* next tick */
        }
      }

      if (!cancelled) {
        timeoutId = setTimeout(tick, POLL_MS);
      }
    };

    tick();

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [conversationId, pollKey]);
}

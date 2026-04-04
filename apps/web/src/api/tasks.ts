import { apiFetch } from "./client";
import type {
  ActionConfirmRequest,
  ActionConfirmResponse,
  TaskResponse,
} from "./types";

export function getTask(taskId: string): Promise<TaskResponse> {
  return apiFetch<TaskResponse>(`/task/${encodeURIComponent(taskId)}`);
}

export function confirmAction(
  body: ActionConfirmRequest,
): Promise<ActionConfirmResponse> {
  return apiFetch<ActionConfirmResponse>("/action/confirm", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

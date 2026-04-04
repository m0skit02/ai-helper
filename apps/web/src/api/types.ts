export type TaskStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "needs_confirmation";

export type ActionStatus =
  | "draft"
  | "waiting_confirm"
  | "sent"
  | "cancelled"
  | "failed";

export type MessageRole = "user" | "assistant" | "tool" | "system";

export type Decision = "approve" | "reject";

export interface TraceItem {
  step: string;
  status: string;
  ts: string;
  tool?: string | null;
}

export interface ActionItem {
  action_id: string;
  type: string;
  status: ActionStatus;
  payload: Record<string, unknown>;
}

export interface TaskResult {
  product: Record<string, unknown> | null;
  news: Array<Record<string, unknown>>;
  sources: string[];
  actions: ActionItem[];
}

export interface TaskResponse {
  task_id: string;
  trace_id: string;
  status: TaskStatus;
  conversation_id?: string | null;
  session_id?: string | null;
  result: TaskResult | null;
  trace: TraceItem[];
  error?: string | null;
}

export interface ConversationResponse {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  items: ConversationResponse[];
}

export interface MessageItem {
  message_id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  task_id?: string | null;
}

export interface ConversationMessagesResponse {
  items: MessageItem[];
}

export interface ConversationMessageCreateRequest {
  content: string;
  allow_social_actions?: boolean;
}

export interface ConversationMessageCreateResponse {
  conversation_id: string;
  user_message: MessageItem;
  assistant_message: MessageItem;
  task: TaskResponse;
}

export interface ActionConfirmRequest {
  task_id: string;
  action_id: string;
  decision: Decision;
}

export interface ActionConfirmResponse {
  task_id: string;
  action_id: string;
  status: ActionStatus;
}

export interface HealthResponse {
  status: string;
}

import { apiFetch } from "./client";
import type {
  ConversationListResponse,
  ConversationMessageCreateRequest,
  ConversationMessageCreateResponse,
  ConversationMessagesResponse,
  ConversationResponse,
} from "./types";

export function listConversations(): Promise<ConversationListResponse> {
  return apiFetch<ConversationListResponse>("/chat/conversations");
}

export function createConversation(
  title?: string,
): Promise<ConversationResponse> {
  return apiFetch<ConversationResponse>("/chat/conversations", {
    method: "POST",
    body: JSON.stringify({ title: title ?? null }),
  });
}

export function getConversation(
  conversationId: string,
): Promise<ConversationResponse> {
  return apiFetch<ConversationResponse>(
    `/chat/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export function listMessages(
  conversationId: string,
): Promise<ConversationMessagesResponse> {
  return apiFetch<ConversationMessagesResponse>(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
  );
}

export function sendMessage(
  conversationId: string,
  body: ConversationMessageCreateRequest,
): Promise<ConversationMessageCreateResponse> {
  return apiFetch<ConversationMessageCreateResponse>(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      body: JSON.stringify({
        content: body.content,
        allow_social_actions: body.allow_social_actions ?? true,
      }),
    },
  );
}

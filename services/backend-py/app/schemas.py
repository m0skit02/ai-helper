from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["queued", "running", "done", "failed", "needs_confirmation"]
Decision = Literal["approve", "reject"]
ActionStatus = Literal["draft", "waiting_confirm", "sent", "cancelled", "failed"]
MessageRole = Literal["user", "assistant", "tool", "system"]


class TaskCreateRequest(BaseModel):
    query: str = Field(min_length=1)
    allow_social_actions: bool = True


class TraceItem(BaseModel):
    step: str
    status: str
    ts: datetime
    tool: str | None = None
    detail: str | None = None


class ActionItem(BaseModel):
    action_id: str
    type: str = "message_send"
    status: ActionStatus = "draft"
    payload: dict[str, Any] = Field(default_factory=dict)


class ProductItem(BaseModel):
    title: str = ""
    price: float | None = None
    currency: str | None = None
    url: str = ""
    seller: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    delivery: str | None = None
    condition: str | None = None
    storage_gb: int | None = None


class NewsItem(BaseModel):
    title: str = ""
    summary: str = ""
    published_at: str | None = None
    url: str = ""
    source: str | None = None


class TaskResult(BaseModel):
    product: ProductItem | None = None
    news: list[NewsItem] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)


class TaskResponse(BaseModel):
    task_id: str
    trace_id: str
    status: TaskStatus
    conversation_id: str | None = None
    session_id: str | None = None
    result: TaskResult | None = None
    trace: list[TraceItem] = Field(default_factory=list)
    error: str | None = None


class ActionConfirmRequest(BaseModel):
    task_id: str
    action_id: str
    decision: Decision


class ActionConfirmResponse(BaseModel):
    task_id: str
    action_id: str
    status: ActionStatus


class ConversationCreateRequest(BaseModel):
    title: str | None = None


class ConversationResponse(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse] = Field(default_factory=list)


class MessageItem(BaseModel):
    message_id: str
    conversation_id: str
    role: MessageRole
    content: str
    created_at: datetime
    task_id: str | None = None


class ConversationMessagesResponse(BaseModel):
    items: list[MessageItem] = Field(default_factory=list)


class ConversationMessageCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    allow_social_actions: bool = True


class ConversationMessageCreateResponse(BaseModel):
    conversation_id: str
    user_message: MessageItem
    assistant_message: MessageItem
    task: TaskResponse

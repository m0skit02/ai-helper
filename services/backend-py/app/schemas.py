from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["queued", "running", "done", "failed", "needs_confirmation"]
Decision = Literal["approve", "reject"]
ActionStatus = Literal["draft", "waiting_confirm", "sent", "cancelled", "failed"]


class TaskCreateRequest(BaseModel):
    query: str = Field(min_length=1)
    allow_social_actions: bool = True


class TraceItem(BaseModel):
    step: str
    status: str
    ts: datetime
    tool: str | None = None


class ActionItem(BaseModel):
    action_id: str
    type: str = "message_send"
    status: ActionStatus = "draft"
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    product: dict[str, Any] | None = None
    news: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)


class TaskResponse(BaseModel):
    task_id: str
    trace_id: str
    status: TaskStatus
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

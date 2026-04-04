from fastapi import FastAPI, HTTPException

from .schemas import (
    ActionConfirmRequest,
    ActionConfirmResponse,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationMessageCreateRequest,
    ConversationMessageCreateResponse,
    ConversationMessagesResponse,
    ConversationResponse,
    TaskCreateRequest,
    TaskResponse,
)
from .llm_client import LLMClient
from .store import TaskStore
from .tools_client import ToolsClient

app = FastAPI(title="ai-helper-backend")
store = TaskStore(tools_client=ToolsClient(), llm_client=LLMClient())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/task", response_model=TaskResponse)
def create_task(req: TaskCreateRequest) -> TaskResponse:
    return store.create_task(req)


@app.get("/task/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/action/confirm", response_model=ActionConfirmResponse)
def confirm_action(req: ActionConfirmRequest) -> ActionConfirmResponse:
    action = store.confirm_action(req)
    if action is None:
        raise HTTPException(status_code=404, detail="Task or action not found")

    return ActionConfirmResponse(
        task_id=req.task_id,
        action_id=req.action_id,
        status=action.status,
    )


@app.post("/chat/conversations", response_model=ConversationResponse)
def create_conversation(req: ConversationCreateRequest) -> ConversationResponse:
    return store.create_conversation(req.title)


@app.get("/chat/conversations", response_model=ConversationListResponse)
def list_conversations() -> ConversationListResponse:
    return ConversationListResponse(items=store.list_conversations())


@app.get("/chat/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(conversation_id: str) -> ConversationResponse:
    conv = store.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.get("/chat/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)
def list_messages(conversation_id: str) -> ConversationMessagesResponse:
    messages = store.list_messages(conversation_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationMessagesResponse(items=messages)


@app.post(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ConversationMessageCreateResponse,
)
def create_message(
    conversation_id: str,
    req: ConversationMessageCreateRequest,
) -> ConversationMessageCreateResponse:
    created = store.add_message_and_create_task(conversation_id, req)
    if created is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return created

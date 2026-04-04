from fastapi import FastAPI, HTTPException

from .schemas import ActionConfirmRequest, ActionConfirmResponse, TaskCreateRequest, TaskResponse
from .store import TaskStore

app = FastAPI(title="ai-helper-backend")
store = TaskStore()


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

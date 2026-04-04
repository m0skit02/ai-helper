# ai-helper

MVP assistant for product/news search and browser actions.

## Current Structure
- `services/backend-py` - FastAPI orchestrator (Developer A)
- `services/tools-go` - browser tools service (Developer C)
- `apps/web` - frontend app (Developer B)
- `extensions/chromium` - browser extension
- `packages/contracts` - shared contracts
- `infra/docker` - docker compose

## First Milestone Status
Implemented in `services/backend-py`:
- `POST /task`
- `GET /task/{task_id}`
- `POST /action/confirm`
- `GET /health`

Message action is universal (`message_send`) and not tied to a specific messenger.

## Run Backend Locally
```bash
cd services/backend-py
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

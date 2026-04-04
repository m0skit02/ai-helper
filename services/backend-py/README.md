# backend-py

FastAPI backend for orchestration and task state management.

## Endpoints (Milestone 1)
- `GET /health`
- `POST /task`
- `GET /task/{task_id}`
- `POST /action/confirm`

## Local Run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Quick Check
```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/task -H "Content-Type: application/json" -d '{"query":"Найди товар","allow_social_actions":true}'
```

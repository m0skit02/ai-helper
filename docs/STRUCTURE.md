# Project Structure

- `services/backend-py`: Python backend + agent orchestration
- `services/tools-go`: Go tools service
- `apps/web`: frontend app
- `extensions/chromium`: browser extension
- `packages/contracts`: shared DTO/contracts
- `infra/docker`: compose setup

## First Milestone (Done)
- Backend API with endpoints:
  - `POST /task`
  - `GET /task/{task_id}`
  - `POST /action/confirm`
  - `GET /health`
- In-memory task store with `trace_id`
- Universal message action flow (`message_send`, confirm required)

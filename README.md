# ai-helper

MVP умного помощника для поиска товаров, новостей и выполнения браузерных действий.

## Текущая структура
- `services/backend-py` - FastAPI-оркестратор (разработчик A)
- `services/tools-go` - браузерный tools-service (разработчик C)
- `apps/web` - frontend (разработчик B)
- `extensions/chromium` - расширение браузера
- `packages/contracts` - общие контракты
- `infra/docker` - docker compose и docker-инфраструктура

## Статус первого этапа
В `services/backend-py` уже реализовано:
- `POST /task`
- `GET /task/{task_id}`
- `POST /action/confirm`
- `GET /health`

Действие отправки сообщения универсальное (`message_send`) и не привязано к конкретному мессенджеру.

## Локальный запуск backend
```bash
cd services/backend-py
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Docker
Локальная сборка для разработчика:
```bash
cd infra/docker
cp .env.example .env
docker compose up --build
```

Публикация готовых образов для команды:
```bash
cd infra/docker
cp .env.example .env
# замените BACKEND_IMAGE / TOOLS_GO_IMAGE / WEB_IMAGE на теги вашего registry
docker compose build
docker compose push
```

Запуск для нового человека через готовые образы:
```bash
cd infra/docker
cp .env.example .env
# укажите в .env опубликованные image tags
docker compose pull
docker compose up -d
```

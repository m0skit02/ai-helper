# Docker-инструкция

## Локальный запуск для разработчика
```bash
cp .env.example .env
docker compose up --build
```

Будут собраны и запущены:
- `backend`
- `tools-go`
- `web`
- `ollama`

## Публикация образов для команды
Открой `.env` и замени значения:
- `BACKEND_IMAGE`
- `TOOLS_GO_IMAGE`
- `WEB_IMAGE`

на теги образов в registry, например:
- `ghcr.io/your-org/ai-helper-backend:latest`
- `ghcr.io/your-org/ai-helper-tools-go:latest`
- `ghcr.io/your-org/ai-helper-web:latest`

После этого выполни:
```bash
docker compose build
docker compose push
```

## Запуск для нового участника команды
```bash
cp .env.example .env
```

Укажи в `.env` те же опубликованные теги образов, затем запусти:
```bash
docker compose pull
docker compose up -d
```

Такой сценарий не требует локальной сборки на новой машине.

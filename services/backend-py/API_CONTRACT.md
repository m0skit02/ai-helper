# Backend API Contract (MVP, In-Memory)

Base URL: `http://127.0.0.1:8000`

LLM behavior:
- If `OPENAI_API_KEY` is set, backend uses LLM for query planning and assistant summary text.
- If no key or LLM call fails, backend uses deterministic fallback logic.

## Chat

### Create conversation
`POST /chat/conversations`
```json
{"title":"Demo chat"}
```

### List conversations
`GET /chat/conversations`

### Get conversation
`GET /chat/conversations/{conversation_id}`

### Get messages
`GET /chat/conversations/{conversation_id}/messages`

### Send user message
`POST /chat/conversations/{conversation_id}/messages`
```json
{"content":"Найди iPhone 256 и отправь сообщение Сергею","allow_social_actions":true}
```

Returns:
- `user_message`
- `assistant_message`
- `task`

## Task

### Get task
`GET /task/{task_id}`

Key fields:
- `status`: `running | needs_confirmation | done | failed`
- `session_id`
- `result.product` (normalized fixed fields)
- `result.news[]` (normalized fixed fields)
- `result.sources[]` (urls)
- `result.actions[]`
- `trace[]`

## Actions

### Confirm action
`POST /action/confirm`
```json
{"task_id":"...","action_id":"...","decision":"approve"}
```

Decision:
- `approve` -> `sent` or `failed`
- `reject` -> `cancelled`

## Health

`GET /health` -> `{"status":"ok"}`

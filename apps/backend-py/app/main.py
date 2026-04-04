from fastapi import FastAPI

app = FastAPI(title="ai-helper-backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

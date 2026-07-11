"""FastAPI application composition owned by Member 1."""

from fastapi import FastAPI

from automation_foundry.authoring.router import router as authoring_router
from automation_foundry.execution.router import router as execution_router


def create_app() -> FastAPI:
    """Create the local Automation Foundry API application."""
    app = FastAPI(title="Computer-Use Automation Foundry", version="0.1.0")
    app.include_router(authoring_router)
    app.include_router(execution_router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

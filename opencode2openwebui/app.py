import os

from orchestrator.api import create_app


app = create_app()


__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("ORCH_HOST", "127.0.0.1"),
        port=int(os.getenv("ORCH_PORT", "8787")),
        reload=False,
    )

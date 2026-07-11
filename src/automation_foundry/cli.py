"""Local loopback server entrypoint."""


def main() -> None:
    """Run the FastAPI service on the loopback interface only."""
    import uvicorn

    uvicorn.run(
        "automation_foundry.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()

import uvicorn

from .config import settings


def main() -> None:
    uvicorn.run(
        "scout_datasets.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()

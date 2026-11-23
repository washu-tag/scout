import sys

import uvicorn

from .config import settings


def main() -> None:
    uvicorn.run(
        "scout_report_viewer.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        timeout_keep_alive=settings.timeout_keep_alive,
        # psycopg's async mode cannot run on Windows' default ProactorEventLoop
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()

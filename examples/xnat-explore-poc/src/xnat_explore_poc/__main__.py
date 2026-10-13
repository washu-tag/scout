import asyncio

import uvicorn

from .app import invoke_app, landing_app
from .config import settings


async def main() -> None:
    # Two servers, not one app on two ports - see app.py's module comment:
    # /invoke must be structurally absent from the public listener.
    invoke_server = uvicorn.Server(
        uvicorn.Config(invoke_app, host=settings.host, port=settings.port)
    )
    landing_server = uvicorn.Server(
        uvicorn.Config(landing_app, host=settings.host, port=settings.landing_page_port)
    )
    await asyncio.gather(invoke_server.serve(), landing_server.serve())


if __name__ == "__main__":
    asyncio.run(main())

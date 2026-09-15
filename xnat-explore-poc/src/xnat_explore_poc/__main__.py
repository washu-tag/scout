import uvicorn

from .app import app
from .config import settings

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)

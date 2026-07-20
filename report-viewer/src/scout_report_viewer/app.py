import logging
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import logging_setup, metrics
from .config import settings
from .db import check_ready, create_pool, ensure_schema
from .routes import config_router, reports_router, searches_router

_VERSION = version("scout_report_viewer")

# Replace stdlib handlers with our JSON formatter BEFORE anything else
# logs. Otherwise FastAPI / uvicorn imports print plain-text lines before
# we get to install the formatter.
logging_setup.configure()

log = logging.getLogger("scout_report_viewer")


class SpaStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for unknown paths so
    React Router's client-side routes work on direct navigation / refresh.

    Without this, hitting /spa/searches/ds_X 404s because there's no such
    file on disk - the SPA expects to handle that route itself."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    try:
        await ensure_schema()
    except Exception:
        # May boot before the DB is provisioned; don't crash-loop.
        # /healthz stays up; /readyz holds traffic until the pool self-heals.
        log.exception("schema bootstrap failed - DB not ready?")
    yield
    await app.state.pool.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Scout Report Viewer Service",
        version=_VERSION,
        description="Surfaces saved searches over Scout reports for the chat iframe.",
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["meta"])
    async def readyz(request: Request) -> dict[str, str]:
        try:
            await check_ready(request.app.state.pool)
        except Exception:
            raise HTTPException(status_code=503, detail="database not ready")
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {
            "service": "report-viewer",
            "version": _VERSION,
            "docs": "/docs",
        }

    app.include_router(config_router)
    app.include_router(searches_router)
    app.include_router(reports_router)

    # html=True falls unmapped /spa/* back to index.html so SPA-router refreshes work.
    _spa_dir = Path(__file__).parent / "static"
    if _spa_dir.is_dir():
        app.mount("/spa", SpaStaticFiles(directory=_spa_dir, html=True), name="spa")
    else:
        log.warning(
            "SPA static directory missing - frontend not bundled into image",
            extra={"path": str(_spa_dir)},
        )

    # /metrics must install AFTER routes so the FastAPI instrumentor
    # sees the final route table for templated-path labels.
    metrics.install(app)

    log.info(
        "report-viewer initialized",
        extra={
            "trino_endpoint": f"{settings.trino_scheme}://{settings.trino_host}:{settings.trino_port}",
            "db_endpoint": settings.database_url.split("@")[-1],
        },
    )
    return app


app = create_app()

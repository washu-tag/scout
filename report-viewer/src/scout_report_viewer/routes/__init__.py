from .config import router as config_router
from .reports import router as reports_router
from .searches import router as searches_router
from .owui_webhook import router as owui_webhook_router

__all__ = ["config_router", "reports_router", "searches_router", "owui_webhook_router"]

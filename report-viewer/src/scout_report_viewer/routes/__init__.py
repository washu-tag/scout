from .config import router as config_router
from .plots import router as plots_router
from .reports import router as reports_router
from .searches import router as searches_router

__all__ = ["config_router", "plots_router", "reports_router", "searches_router"]

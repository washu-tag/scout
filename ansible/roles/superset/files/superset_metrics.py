"""
Superset database metrics for Prometheus.

Provides connection pool metrics for Superset's metadata database.

Metrics exposed:
- superset_db_pool_size: Configured pool size
- superset_db_pool_checked_in: Available connections in pool
- superset_db_pool_checked_out: Connections currently in use
- superset_db_pool_overflow: Overflow connections in use (always >= 0)
"""

import logging
import threading

from flask import Flask
from prometheus_client import Gauge, make_wsgi_app
from sqlalchemy import event
from sqlalchemy.pool import Pool
from werkzeug.middleware.dispatcher import DispatcherMiddleware

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Metric Definitions
# -----------------------------------------------------------------------------

# Connection pool metrics (Gauges)
db_pool_size = Gauge(
    "superset_db_pool_size",
    "Database connection pool size",
)
db_pool_checked_in = Gauge(
    "superset_db_pool_checked_in",
    "Checked in (available) connections",
)
db_pool_checked_out = Gauge(
    "superset_db_pool_checked_out",
    "Checked out (in use) connections",
)
db_pool_overflow = Gauge(
    "superset_db_pool_overflow",
    "Overflow connections currently in use",
)


# -----------------------------------------------------------------------------
# Pool Metrics
# -----------------------------------------------------------------------------


def _update_pool_metrics(pool) -> None:
    """Update all pool metrics for a given pool."""
    try:
        pool_size = pool.size()
        checked_in = pool.checkedin()
        checked_out = pool.checkedout()
        # Fix: Calculate actual overflow in use (never negative)
        overflow = max(0, checked_out - pool_size)

        db_pool_size.set(pool_size)
        db_pool_checked_in.set(checked_in)
        db_pool_checked_out.set(checked_out)
        db_pool_overflow.set(overflow)
    except Exception as e:
        logger.debug(f"Error updating pool metrics: {e}")


@event.listens_for(Pool, "connect")
def on_pool_connect(dbapi_conn, connection_record):
    """Called when a new DBAPI connection is created."""
    try:
        pool = getattr(connection_record, "_pool", None)
        if pool is None:
            # Fallback: try to get pool from Superset's db
            from superset import db

            pool = db.engine.pool

        if pool:
            _update_pool_metrics(pool)
    except Exception as e:
        logger.debug(f"Error in on_pool_connect: {e}")


@event.listens_for(Pool, "checkout")
def on_pool_checkout(dbapi_conn, connection_record, connection_proxy):
    """Called when a connection is retrieved from the pool."""
    try:
        pool = getattr(connection_record, "_pool", None)
        if pool is None:
            from superset import db

            pool = db.engine.pool

        if pool:
            _update_pool_metrics(pool)
    except Exception as e:
        logger.debug(f"Error in on_pool_checkout: {e}")


@event.listens_for(Pool, "checkin")
def on_pool_checkin(dbapi_conn, connection_record):
    """Called when a connection is returned to the pool."""
    try:
        pool = getattr(connection_record, "_pool", None)
        if pool is None:
            from superset import db

            pool = db.engine.pool

        if pool:
            _update_pool_metrics(pool)
    except Exception as e:
        logger.debug(f"Error in on_pool_checkin: {e}")


# -----------------------------------------------------------------------------
# Periodic Pool Metrics Update
# -----------------------------------------------------------------------------


def _periodic_pool_update(interval: int = 15) -> None:
    """
    Periodically update pool metrics for Superset's main database.

    This ensures metrics are fresh even if no pool events occur.
    """
    while True:
        try:
            from superset import db

            pool = db.engine.pool
            _update_pool_metrics(pool)
        except Exception as e:
            logger.debug(f"Error in periodic pool update: {e}")

        # Sleep for the configured interval
        threading.Event().wait(interval)


# -----------------------------------------------------------------------------
# Flask Integration
# -----------------------------------------------------------------------------


def setup_metrics(app: Flask) -> Flask:
    """
    Configure Prometheus metrics endpoint and start background tasks.

    This function is called by Superset via FLASK_APP_MUTATOR.
    """
    # Start periodic pool metrics update thread
    metrics_thread = threading.Thread(
        target=_periodic_pool_update, args=(15,), daemon=True, name="superset-metrics"
    )
    metrics_thread.start()
    logger.info("Started Superset metrics collection thread")

    # Add Prometheus metrics endpoint at /metrics
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/metrics": make_wsgi_app()})

    return app


# Register the setup function to be called after app initialization
FLASK_APP_MUTATOR = setup_metrics

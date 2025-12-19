"""
Superset database metrics for Prometheus.

Provides connection pool metrics, query timing, and error tracking
for all database connections (Superset metadata DB and data sources).

Metrics exposed:
- superset_db_pool_size: Configured pool size
- superset_db_pool_checked_in: Available connections in pool
- superset_db_pool_checked_out: Connections currently in use
- superset_db_pool_overflow: Overflow connections in use (always >= 0)
- superset_db_query_duration_seconds: Query latency histogram
- superset_db_errors_total: Database error counter
"""

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from flask import Flask
from prometheus_client import Counter, Gauge, Histogram, make_wsgi_app
from sqlalchemy import event
from sqlalchemy.engine import Engine
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
    ["database"],
)
db_pool_checked_in = Gauge(
    "superset_db_pool_checked_in",
    "Checked in (available) connections",
    ["database"],
)
db_pool_checked_out = Gauge(
    "superset_db_pool_checked_out",
    "Checked out (in use) connections",
    ["database"],
)
db_pool_overflow = Gauge(
    "superset_db_pool_overflow",
    "Overflow connections currently in use",
    ["database"],
)

# Query timing metric (Histogram)
db_query_duration_seconds = Histogram(
    "superset_db_query_duration_seconds",
    "Database query duration in seconds",
    ["database", "status"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

# Error counter
db_errors_total = Counter(
    "superset_db_errors_total",
    "Database errors",
    ["database", "error_type", "is_disconnect"],
)

# -----------------------------------------------------------------------------
# Database Label Extraction
# -----------------------------------------------------------------------------

# Cache for engine -> database label mapping
_engine_labels: dict = {}
_engine_labels_lock = threading.Lock()

# Registry for pool -> database label mapping (more reliable than deriving from pool)
_pool_label_registry: dict = {}
_pool_label_registry_lock = threading.Lock()


def register_pool_label(pool, label: str) -> None:
    """Register a pool with its database label for reliable lookup."""
    with _pool_label_registry_lock:
        _pool_label_registry[id(pool)] = label
        logger.debug(f"Registered pool {id(pool)} with label '{label}'")


def get_database_label(engine_or_url) -> str:
    """
    Extract a human-readable database label from an engine or URL.

    Returns labels like:
    - "postgresql" for Superset's metadata database
    - "trino" for Trino connections
    - "mysql", "sqlite", etc. for other databases
    - "unknown" if unable to determine
    """
    try:
        # Handle Engine objects
        if hasattr(engine_or_url, "url"):
            url = engine_or_url.url
        elif hasattr(engine_or_url, "engine"):
            url = engine_or_url.engine.url
        else:
            url = engine_or_url

        # Get the dialect/driver name
        if hasattr(url, "drivername"):
            drivername = url.drivername
        elif hasattr(url, "get_dialect"):
            drivername = url.get_dialect().name
        else:
            # Try parsing as string
            parsed = urlparse(str(url))
            drivername = parsed.scheme.split("+")[0] if parsed.scheme else "unknown"

        # Normalize common driver names
        if drivername:
            # Handle dialect+driver format (e.g., "postgresql+psycopg2")
            dialect = drivername.split("+")[0].lower()
            return dialect

        return "unknown"
    except Exception:
        return "unknown"


def _get_label_for_pool(pool) -> str:
    """Get database label for a connection pool."""
    pool_id = id(pool)

    # First check the explicit registry (most reliable)
    with _pool_label_registry_lock:
        if pool_id in _pool_label_registry:
            return _pool_label_registry[pool_id]

    # Then check the engine labels cache
    with _engine_labels_lock:
        if pool_id in _engine_labels:
            return _engine_labels[pool_id]

    # Try to find the engine for this pool (fallback)
    label = "unknown"
    try:
        # Pool has a reference to its creator (engine)
        if hasattr(pool, "_creator") and hasattr(pool._creator, "__self__"):
            engine = pool._creator.__self__
            label = get_database_label(engine)
        elif hasattr(pool, "engine"):
            label = get_database_label(pool.engine)
    except Exception:
        pass

    # Cache in the registry for future lookups
    with _pool_label_registry_lock:
        _pool_label_registry[pool_id] = label

    return label


def _get_label_for_connection(connection) -> str:
    """Get database label from a connection object."""
    try:
        if hasattr(connection, "engine"):
            return get_database_label(connection.engine)
        elif hasattr(connection, "dialect"):
            return connection.dialect.name
    except Exception:
        pass
    return "unknown"


# -----------------------------------------------------------------------------
# Pool Metrics
# -----------------------------------------------------------------------------


def _update_pool_metrics(pool, database_label: str) -> None:
    """Update all pool metrics for a given pool."""
    try:
        pool_size = pool.size()
        checked_in = pool.checkedin()
        checked_out = pool.checkedout()
        # Fix: Calculate actual overflow in use (never negative)
        overflow = max(0, checked_out - pool_size)

        db_pool_size.labels(database=database_label).set(pool_size)
        db_pool_checked_in.labels(database=database_label).set(checked_in)
        db_pool_checked_out.labels(database=database_label).set(checked_out)
        db_pool_overflow.labels(database=database_label).set(overflow)
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
            label = _get_label_for_pool(pool)
            _update_pool_metrics(pool, label)
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
            label = _get_label_for_pool(pool)
            logger.info(f"METRICS: Pool checkout for database={label}")
            _update_pool_metrics(pool, label)
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
            label = _get_label_for_pool(pool)
            _update_pool_metrics(pool, label)
    except Exception as e:
        logger.debug(f"Error in on_pool_checkin: {e}")


# -----------------------------------------------------------------------------
# Query Timing
# -----------------------------------------------------------------------------


@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Record query start time before execution."""
    try:
        label = _get_label_for_connection(conn)
        logger.info(f"METRICS: Query starting for database={label}")
        context._query_start_time = time.perf_counter()
        context._query_database_label = label
    except Exception as e:
        logger.warning(f"Error in before_cursor_execute: {e}")


@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Record query duration after successful execution."""
    try:
        start_time = getattr(context, "_query_start_time", None)
        if start_time is not None:
            duration = time.perf_counter() - start_time
            # Use cached label if available, otherwise derive it
            label = getattr(context, "_query_database_label", None)
            if label is None:
                label = _get_label_for_connection(conn)
            logger.info(
                f"METRICS: Query completed for database={label}, duration={duration:.3f}s"
            )
            db_query_duration_seconds.labels(database=label, status="success").observe(
                duration
            )
    except Exception as e:
        logger.warning(f"Error in after_cursor_execute: {e}")


# -----------------------------------------------------------------------------
# Error Tracking
# -----------------------------------------------------------------------------


@event.listens_for(Engine, "handle_error")
def handle_error(exception_context):
    """Called when a database error occurs."""
    try:
        # Get error details
        original_exception = exception_context.original_exception
        error_type = type(original_exception).__name__
        is_disconnect = str(exception_context.is_disconnect).lower()

        # Get database label
        label = "unknown"
        if exception_context.connection is not None:
            label = _get_label_for_connection(exception_context.connection)
        elif exception_context.engine is not None:
            label = get_database_label(exception_context.engine)

        # Record the error
        db_errors_total.labels(
            database=label, error_type=error_type, is_disconnect=is_disconnect
        ).inc()

        # Also record timing if we have it (query failed partway through)
        if hasattr(exception_context, "execution_context"):
            exec_ctx = exception_context.execution_context
            if exec_ctx is not None:
                start_time = getattr(exec_ctx, "_query_start_time", None)
                if start_time is not None:
                    duration = time.perf_counter() - start_time
                    db_query_duration_seconds.labels(
                        database=label, status="error"
                    ).observe(duration)

        logger.debug(
            f"Database error recorded: database={label}, "
            f"error_type={error_type}, is_disconnect={is_disconnect}"
        )
    except Exception as e:
        logger.debug(f"Error in handle_error: {e}")


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
            label = get_database_label(db.engine)
            # Register the pool label for reliable lookup in event handlers
            register_pool_label(pool, label)
            _update_pool_metrics(pool, label)
        except Exception as e:
            logger.debug(f"Error in periodic pool update: {e}")

        # Sleep for the configured interval
        threading.Event().wait(interval)


# -----------------------------------------------------------------------------
# Flask Integration
# -----------------------------------------------------------------------------


def _instrument_database_engines() -> None:
    """
    Instrument Superset's Database methods to capture data source metrics.

    This instruments:
    1. get_sqla_engine() - to register pools with correct labels
    2. get_raw_connection() - to track query execution (since Superset bypasses SQLAlchemy events)
    """
    try:
        from contextlib import contextmanager
        from superset.models.core import Database

        # Store the original methods
        _original_get_sqla_engine = Database.get_sqla_engine
        _original_get_raw_connection = Database.get_raw_connection

        def _instrumented_get_sqla_engine(self, *args, **kwargs):
            """Wrapped get_sqla_engine that registers the engine's pool."""
            engine = _original_get_sqla_engine(self, *args, **kwargs)
            try:
                # Use self.backend (e.g., "trino", "postgresql") from Superset's Database object
                label = getattr(self, "backend", None) or get_database_label(engine)
                if hasattr(engine, "pool") and engine.pool is not None:
                    register_pool_label(engine.pool, label)
                logger.info(
                    f"METRICS: Instrumented engine created for database={label}"
                )
            except Exception as e:
                logger.warning(f"Error registering engine pool: {e}")
            return engine

        @contextmanager
        def _instrumented_get_raw_connection(self, *args, **kwargs):
            """Wrapped get_raw_connection that tracks connection usage and query timing."""
            label = getattr(self, "backend", None) or "unknown"
            start_time = time.perf_counter()
            logger.info(f"METRICS: Raw connection starting for database={label}")

            try:
                with _original_get_raw_connection(self, *args, **kwargs) as conn:
                    yield conn
            except Exception as e:
                # Record error
                error_type = type(e).__name__
                db_errors_total.labels(
                    database=label, error_type=error_type, is_disconnect="true"
                ).inc()
                duration = time.perf_counter() - start_time
                db_query_duration_seconds.labels(
                    database=label, status="error"
                ).observe(duration)
                logger.info(
                    f"METRICS: Raw connection error for database={label}, error={error_type}, duration={duration:.3f}s"
                )
                raise
            else:
                # Record success
                duration = time.perf_counter() - start_time
                db_query_duration_seconds.labels(
                    database=label, status="success"
                ).observe(duration)
                logger.info(
                    f"METRICS: Raw connection completed for database={label}, duration={duration:.3f}s"
                )

        # Replace the methods
        Database.get_sqla_engine = _instrumented_get_sqla_engine
        Database.get_raw_connection = _instrumented_get_raw_connection
        logger.info(
            "Instrumented Database.get_sqla_engine() and get_raw_connection() for metrics collection"
        )
    except ImportError as e:
        logger.debug(f"Could not instrument Database methods: {e}")
    except Exception as e:
        logger.warning(f"Error instrumenting Database methods: {e}")


def setup_metrics(app: Flask) -> Flask:
    """
    Configure Prometheus metrics endpoint and start background tasks.

    This function is called by Superset via FLASK_APP_MUTATOR.
    """
    # Instrument Superset's Database class to capture data source engines
    _instrument_database_engines()

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

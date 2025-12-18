"""
Unit tests for superset_metrics.

Run with: uvx pytest ansible/roles/superset/files/test_superset_metrics.py -v --tb=short

Note: These tests mock Flask, prometheus_client, and SQLAlchemy at import time
to test the metrics logic without requiring actual dependencies installed.
"""

import sys
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# -----------------------------------------------------------------------------
# Mock Dependencies at Import Time
# -----------------------------------------------------------------------------

# Create mock modules before importing superset_metrics
mock_flask = MagicMock()
mock_flask.Flask = MagicMock

mock_prometheus = MagicMock()
mock_prometheus.Gauge = MagicMock
mock_prometheus.Counter = MagicMock
mock_prometheus.Histogram = MagicMock
mock_prometheus.make_wsgi_app = MagicMock(return_value=MagicMock())

mock_werkzeug = MagicMock()
mock_werkzeug.middleware = MagicMock()
mock_werkzeug.middleware.dispatcher = MagicMock()
mock_werkzeug.middleware.dispatcher.DispatcherMiddleware = MagicMock

mock_sqlalchemy = MagicMock()
mock_sqlalchemy.event = MagicMock()
mock_sqlalchemy.event.listens_for = MagicMock(
    side_effect=lambda *args, **kwargs: lambda f: f
)

mock_sqlalchemy_engine = MagicMock()
mock_sqlalchemy_engine.Engine = MagicMock

mock_sqlalchemy_pool = MagicMock()
mock_sqlalchemy_pool.Pool = MagicMock

mock_superset_module = MagicMock()
mock_superset_module.db = MagicMock()
mock_superset_module.db.engine.pool = MagicMock()

# Patch sys.modules before importing superset_metrics
sys.modules["flask"] = mock_flask
sys.modules["prometheus_client"] = mock_prometheus
sys.modules["werkzeug"] = mock_werkzeug
sys.modules["werkzeug.middleware"] = mock_werkzeug.middleware
sys.modules["werkzeug.middleware.dispatcher"] = mock_werkzeug.middleware.dispatcher
sys.modules["sqlalchemy"] = mock_sqlalchemy
sys.modules["sqlalchemy.event"] = mock_sqlalchemy.event
sys.modules["sqlalchemy.engine"] = mock_sqlalchemy_engine
sys.modules["sqlalchemy.pool"] = mock_sqlalchemy_pool
sys.modules["superset"] = mock_superset_module


# Now we can create real mock classes for testing
class MockGauge:
    """Mock Prometheus Gauge metric."""

    def __init__(self, name, description, labelnames=None):
        self.name = name
        self.description = description
        self._labelnames = labelnames or []
        self._values = {}

    def labels(self, **kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in self._values:
            self._values[key] = MockGaugeValue()
        return self._values[key]


class MockGaugeValue:
    """Mock Gauge value with set method."""

    def __init__(self):
        self.value = 0

    def set(self, value):
        self.value = value


class MockCounter:
    """Mock Prometheus Counter metric."""

    def __init__(self, name, description, labelnames=None):
        self.name = name
        self.description = description
        self._labelnames = labelnames or []
        self._values = {}

    def labels(self, **kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in self._values:
            self._values[key] = MockCounterValue()
        return self._values[key]


class MockCounterValue:
    """Mock Counter value with inc method."""

    def __init__(self):
        self.value = 0

    def inc(self, amount=1):
        self.value += amount


class MockHistogram:
    """Mock Prometheus Histogram metric."""

    def __init__(self, name, description, labelnames=None, buckets=None):
        self.name = name
        self.description = description
        self._labelnames = labelnames or []
        self._buckets = buckets or []
        self._values = {}

    def labels(self, **kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in self._values:
            self._values[key] = MockHistogramValue()
        return self._values[key]


class MockHistogramValue:
    """Mock Histogram value with observe method."""

    def __init__(self):
        self.observations = []

    def observe(self, value):
        self.observations.append(value)


# Replace mock prometheus classes with our test implementations
mock_prometheus.Gauge = MockGauge
mock_prometheus.Counter = MockCounter
mock_prometheus.Histogram = MockHistogram


# Now import the module under test (after mocking)
# Clear cache first
if "superset_metrics" in sys.modules:
    del sys.modules["superset_metrics"]

import superset_metrics


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def fresh_metrics():
    """Create fresh metric instances for each test."""
    # Reset the module-level metrics to fresh instances
    superset_metrics.db_pool_size = MockGauge(
        "superset_db_pool_size", "Pool size", ["database"]
    )
    superset_metrics.db_pool_checked_in = MockGauge(
        "superset_db_pool_checked_in", "Checked in", ["database"]
    )
    superset_metrics.db_pool_checked_out = MockGauge(
        "superset_db_pool_checked_out", "Checked out", ["database"]
    )
    superset_metrics.db_pool_overflow = MockGauge(
        "superset_db_pool_overflow", "Overflow", ["database"]
    )
    superset_metrics.db_query_duration_seconds = MockHistogram(
        "superset_db_query_duration_seconds",
        "Query duration",
        ["database", "status"],
    )
    superset_metrics.db_errors_total = MockCounter(
        "superset_db_errors_total",
        "Errors",
        ["database", "error_type", "is_disconnect"],
    )
    # Clear label cache
    superset_metrics._engine_labels.clear()
    return superset_metrics


@pytest.fixture
def mock_pool():
    """Create a mock SQLAlchemy connection pool."""
    pool = MagicMock()
    pool.size.return_value = 30
    pool.checkedin.return_value = 25
    pool.checkedout.return_value = 5
    return pool


# -----------------------------------------------------------------------------
# Database Label Tests
# -----------------------------------------------------------------------------


class TestGetDatabaseLabel:
    """Test database label extraction from engines and URLs."""

    def test_postgresql_url(self, fresh_metrics):
        """PostgreSQL URLs should return 'postgresql'."""
        mock_engine = MagicMock()
        mock_engine.url.drivername = "postgresql+psycopg2"
        assert fresh_metrics.get_database_label(mock_engine) == "postgresql"

    def test_trino_url(self, fresh_metrics):
        """Trino URLs should return 'trino'."""
        mock_engine = MagicMock()
        mock_engine.url.drivername = "trino"
        assert fresh_metrics.get_database_label(mock_engine) == "trino"

    def test_mysql_url(self, fresh_metrics):
        """MySQL URLs should return 'mysql'."""
        mock_engine = MagicMock()
        mock_engine.url.drivername = "mysql+pymysql"
        assert fresh_metrics.get_database_label(mock_engine) == "mysql"

    def test_sqlite_url(self, fresh_metrics):
        """SQLite URLs should return 'sqlite'."""
        mock_engine = MagicMock()
        mock_engine.url.drivername = "sqlite"
        assert fresh_metrics.get_database_label(mock_engine) == "sqlite"

    def test_unknown_on_exception(self, fresh_metrics):
        """Should return 'unknown' when extraction fails."""
        mock_engine = MagicMock()
        mock_engine.url = None
        del mock_engine.engine
        assert fresh_metrics.get_database_label(mock_engine) == "unknown"

    def test_url_without_drivername(self, fresh_metrics):
        """Should handle URL objects without drivername attribute."""
        mock_url = MagicMock(spec=[])  # No attributes
        assert fresh_metrics.get_database_label(mock_url) == "unknown"


# -----------------------------------------------------------------------------
# Pool Overflow Calculation Tests
# -----------------------------------------------------------------------------


class TestPoolOverflowCalculation:
    """Test that overflow is calculated correctly (never negative)."""

    def test_overflow_zero_when_under_capacity(self, fresh_metrics, mock_pool):
        """Overflow should be 0 when checked_out < pool_size."""
        mock_pool.size.return_value = 30
        mock_pool.checkedout.return_value = 5  # Under capacity

        fresh_metrics._update_pool_metrics(mock_pool, "test_db")

        # Overflow should be max(0, 5 - 30) = 0
        gauge_value = fresh_metrics.db_pool_overflow.labels(database="test_db")
        assert gauge_value.value == 0

    def test_overflow_positive_when_over_capacity(self, fresh_metrics, mock_pool):
        """Overflow should be positive when checked_out > pool_size."""
        mock_pool.size.return_value = 30
        mock_pool.checkedout.return_value = 35  # Over capacity

        fresh_metrics._update_pool_metrics(mock_pool, "test_db")

        # Overflow should be max(0, 35 - 30) = 5
        gauge_value = fresh_metrics.db_pool_overflow.labels(database="test_db")
        assert gauge_value.value == 5

    def test_overflow_zero_at_exact_capacity(self, fresh_metrics, mock_pool):
        """Overflow should be 0 when checked_out == pool_size."""
        mock_pool.size.return_value = 30
        mock_pool.checkedout.return_value = 30  # Exactly at capacity

        fresh_metrics._update_pool_metrics(mock_pool, "test_db")

        # Overflow should be max(0, 30 - 30) = 0
        gauge_value = fresh_metrics.db_pool_overflow.labels(database="test_db")
        assert gauge_value.value == 0


# -----------------------------------------------------------------------------
# Pool Event Tests
# -----------------------------------------------------------------------------


class TestPoolEvents:
    """Test pool event handlers update metrics correctly."""

    def test_checkout_updates_pool_metrics(self, fresh_metrics, mock_pool):
        """Checkout should update all pool metrics."""
        mock_pool.size.return_value = 30
        mock_pool.checkedin.return_value = 24
        mock_pool.checkedout.return_value = 6

        mock_connection_record = MagicMock()
        mock_connection_record._pool = mock_pool

        with patch.object(
            fresh_metrics, "_get_label_for_pool", return_value="postgresql"
        ):
            fresh_metrics.on_pool_checkout(
                MagicMock(), mock_connection_record, MagicMock()
            )

        # Verify metrics were updated
        assert fresh_metrics.db_pool_size.labels(database="postgresql").value == 30
        assert (
            fresh_metrics.db_pool_checked_in.labels(database="postgresql").value == 24
        )
        assert (
            fresh_metrics.db_pool_checked_out.labels(database="postgresql").value == 6
        )

    def test_checkin_updates_pool_metrics(self, fresh_metrics, mock_pool):
        """Checkin should update all pool metrics."""
        mock_pool.size.return_value = 30
        mock_pool.checkedin.return_value = 28
        mock_pool.checkedout.return_value = 2

        mock_connection_record = MagicMock()
        mock_connection_record._pool = mock_pool

        with patch.object(fresh_metrics, "_get_label_for_pool", return_value="trino"):
            fresh_metrics.on_pool_checkin(MagicMock(), mock_connection_record)

        assert fresh_metrics.db_pool_checked_in.labels(database="trino").value == 28

    def test_event_handles_missing_pool_gracefully(self, fresh_metrics):
        """Events should not raise when pool is unavailable."""
        mock_connection_record = MagicMock()
        mock_connection_record._pool = None

        # Mock the superset.db import inside the function
        mock_superset_module.db.engine.pool = MagicMock()
        mock_superset_module.db.engine.pool.size.return_value = 10
        mock_superset_module.db.engine.pool.checkedin.return_value = 10
        mock_superset_module.db.engine.pool.checkedout.return_value = 0

        # Should not raise
        fresh_metrics.on_pool_checkout(MagicMock(), mock_connection_record, MagicMock())


# -----------------------------------------------------------------------------
# Query Timing Tests
# -----------------------------------------------------------------------------


class TestQueryTiming:
    """Test query timing metrics."""

    def test_before_cursor_sets_start_time(self, fresh_metrics):
        """before_cursor_execute should set _query_start_time on context."""
        mock_context = MagicMock(spec=[])

        fresh_metrics.before_cursor_execute(
            MagicMock(), MagicMock(), "SELECT 1", None, mock_context, False
        )

        assert hasattr(mock_context, "_query_start_time")
        assert isinstance(mock_context._query_start_time, float)

    def test_after_cursor_records_duration(self, fresh_metrics):
        """after_cursor_execute should record query duration."""
        mock_context = MagicMock()
        mock_context._query_start_time = time.perf_counter() - 0.5  # 500ms ago

        mock_conn = MagicMock()

        with patch.object(
            fresh_metrics, "_get_label_for_connection", return_value="trino"
        ):
            fresh_metrics.after_cursor_execute(
                mock_conn, MagicMock(), "SELECT 1", None, mock_context, False
            )

        # Check that observation was recorded
        histogram = fresh_metrics.db_query_duration_seconds
        observations = histogram.labels(database="trino", status="success").observations
        assert len(observations) == 1
        assert observations[0] >= 0.4  # Should be ~0.5s

    def test_after_cursor_handles_missing_start_time(self, fresh_metrics):
        """Should not crash if _query_start_time is missing."""
        mock_context = MagicMock(spec=[])  # No _query_start_time

        # Should not raise
        fresh_metrics.after_cursor_execute(
            MagicMock(), MagicMock(), "SELECT 1", None, mock_context, False
        )


# -----------------------------------------------------------------------------
# Error Tracking Tests
# -----------------------------------------------------------------------------


class TestErrorTracking:
    """Test error tracking metrics."""

    def test_connection_error_is_disconnect_true(self, fresh_metrics):
        """Connection errors should have is_disconnect='true'."""
        mock_exception_context = MagicMock()
        mock_exception_context.original_exception = ConnectionError("Connection lost")
        mock_exception_context.is_disconnect = True
        mock_exception_context.connection = MagicMock()
        mock_exception_context.engine = None
        mock_exception_context.execution_context = None

        with patch.object(
            fresh_metrics, "_get_label_for_connection", return_value="trino"
        ):
            fresh_metrics.handle_error(mock_exception_context)

        # Check counter was incremented
        counter_value = fresh_metrics.db_errors_total.labels(
            database="trino", error_type="ConnectionError", is_disconnect="true"
        )
        assert counter_value.value == 1

    def test_query_error_is_disconnect_false(self, fresh_metrics):
        """Query errors should have is_disconnect='false'."""
        mock_exception_context = MagicMock()
        mock_exception_context.original_exception = ValueError("Invalid query")
        mock_exception_context.is_disconnect = False
        mock_exception_context.connection = MagicMock()
        mock_exception_context.engine = None
        mock_exception_context.execution_context = None

        with patch.object(
            fresh_metrics, "_get_label_for_connection", return_value="postgresql"
        ):
            fresh_metrics.handle_error(mock_exception_context)

        counter_value = fresh_metrics.db_errors_total.labels(
            database="postgresql", error_type="ValueError", is_disconnect="false"
        )
        assert counter_value.value == 1

    def test_error_records_duration_if_available(self, fresh_metrics):
        """Errors should record query duration if start time was set."""
        mock_exec_context = MagicMock()
        mock_exec_context._query_start_time = time.perf_counter() - 1.0

        mock_exception_context = MagicMock()
        mock_exception_context.original_exception = TimeoutError("Query timeout")
        mock_exception_context.is_disconnect = False
        mock_exception_context.connection = MagicMock()
        mock_exception_context.engine = None
        mock_exception_context.execution_context = mock_exec_context

        with patch.object(
            fresh_metrics, "_get_label_for_connection", return_value="trino"
        ):
            fresh_metrics.handle_error(mock_exception_context)

        # Check duration was recorded for error status
        histogram = fresh_metrics.db_query_duration_seconds
        observations = histogram.labels(database="trino", status="error").observations
        assert len(observations) == 1
        assert observations[0] >= 0.9  # Should be ~1.0s

    def test_error_type_captures_exception_class_name(self, fresh_metrics):
        """error_type label should be the exception class name."""

        class CustomDatabaseError(Exception):
            pass

        mock_exception_context = MagicMock()
        mock_exception_context.original_exception = CustomDatabaseError("Custom error")
        mock_exception_context.is_disconnect = False
        mock_exception_context.connection = MagicMock()
        mock_exception_context.engine = None
        mock_exception_context.execution_context = None

        with patch.object(
            fresh_metrics, "_get_label_for_connection", return_value="test"
        ):
            fresh_metrics.handle_error(mock_exception_context)

        # Verify the error type label
        counter = fresh_metrics.db_errors_total
        # Check that we have a value with CustomDatabaseError as the error_type
        found = False
        for key, value in counter._values.items():
            if ("error_type", "CustomDatabaseError") in key and value.value > 0:
                found = True
                break
        assert found, "CustomDatabaseError not found in error counter"


# -----------------------------------------------------------------------------
# Flask Integration Tests
# -----------------------------------------------------------------------------


class TestFlaskIntegration:
    """Test Flask app setup and integration."""

    def test_setup_metrics_returns_app(self, fresh_metrics):
        """setup_metrics should return the app."""
        mock_app = MagicMock()
        # Use a real object for wsgi_app, not a MagicMock
        mock_app.wsgi_app = lambda environ, start_response: None

        # Mock DispatcherMiddleware to avoid spec issues
        with patch.object(
            fresh_metrics, "DispatcherMiddleware", return_value=MagicMock()
        ):
            with patch.object(fresh_metrics.threading, "Thread") as mock_thread:
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                result = fresh_metrics.setup_metrics(mock_app)

        assert result is mock_app

    def test_setup_metrics_starts_daemon_thread(self, fresh_metrics):
        """setup_metrics should start a daemon thread for periodic updates."""
        mock_app = MagicMock()
        mock_app.wsgi_app = lambda environ, start_response: None

        with patch.object(
            fresh_metrics, "DispatcherMiddleware", return_value=MagicMock()
        ):
            with patch.object(fresh_metrics.threading, "Thread") as mock_thread:
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                fresh_metrics.setup_metrics(mock_app)

                mock_thread.assert_called_once()
                call_kwargs = mock_thread.call_args[1]
                assert call_kwargs["daemon"] is True
                assert call_kwargs["name"] == "superset-metrics"
                mock_thread_instance.start.assert_called_once()

    def test_flask_app_mutator_is_set(self, fresh_metrics):
        """FLASK_APP_MUTATOR should be set to setup_metrics."""
        assert fresh_metrics.FLASK_APP_MUTATOR is fresh_metrics.setup_metrics


# -----------------------------------------------------------------------------
# Thread Safety Tests
# -----------------------------------------------------------------------------


class TestThreadSafety:
    """Test thread safety of label caching."""

    def test_engine_labels_cache_is_thread_safe(self, fresh_metrics):
        """Multiple threads should safely access the label cache."""
        results = []
        errors = []

        def worker(engine_id):
            try:
                # Create a mock pool with proper structure
                mock_engine = MagicMock()
                mock_engine.url.drivername = f"db_{engine_id}"

                mock_pool = MagicMock()
                mock_pool.engine = mock_engine

                # Simulate concurrent access
                for _ in range(10):
                    label = fresh_metrics._get_label_for_pool(mock_pool)
                    results.append(label)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(results) == 50  # 5 threads * 10 iterations


# -----------------------------------------------------------------------------
# Edge Cases
# -----------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_update_pool_metrics_handles_none_pool(self, fresh_metrics):
        """_update_pool_metrics should handle None pool gracefully."""
        # Should not raise, just log a debug message
        fresh_metrics._update_pool_metrics(None, "test")

    def test_all_metrics_use_database_label(self, fresh_metrics):
        """Verify all metrics accept the database label."""
        # Pool metrics
        fresh_metrics.db_pool_size.labels(database="test")
        fresh_metrics.db_pool_checked_in.labels(database="test")
        fresh_metrics.db_pool_checked_out.labels(database="test")
        fresh_metrics.db_pool_overflow.labels(database="test")

        # Query metrics
        fresh_metrics.db_query_duration_seconds.labels(
            database="test", status="success"
        )

        # Error metrics
        fresh_metrics.db_errors_total.labels(
            database="test", error_type="Error", is_disconnect="false"
        )

    def test_query_duration_accepts_status_label(self, fresh_metrics):
        """Query duration histogram should accept status label."""
        fresh_metrics.db_query_duration_seconds.labels(
            database="test", status="success"
        ).observe(1.0)
        fresh_metrics.db_query_duration_seconds.labels(
            database="test", status="error"
        ).observe(0.5)

    def test_errors_accept_all_labels(self, fresh_metrics):
        """Error counter should accept all required labels."""
        fresh_metrics.db_errors_total.labels(
            database="trino", error_type="TimeoutError", is_disconnect="true"
        ).inc()

        counter = fresh_metrics.db_errors_total.labels(
            database="trino", error_type="TimeoutError", is_disconnect="true"
        )
        assert counter.value == 1

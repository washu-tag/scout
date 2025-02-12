import os
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

exporter = OTLPMetricExporter(
    endpoint="http://prometheus-server.prometheus.svc.cluster.local:9090/api/v1/otlp/v1/metrics"
)

metric_reader = PeriodicExportingMetricReader(exporter)

resource = Resource(
    attributes={
        SERVICE_NAME: "temporal-python",
        "hostname": os.getenv("HOSTNAME", "unknown"),
    }
)

meterProvider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meterProvider)
meter = metrics.get_meter("temporal-python")

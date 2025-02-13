from opentelemetry import metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

exporter = OTLPMetricExporter(
    endpoint="http://prometheus-server.prometheus.svc.cluster.local:9090/api/v1/otlp/v1/metrics"
)

trace_exporter = OTLPSpanExporter(
    endpoint="http://jaeger-collector.jaeger.svc.cluster.local:4318/v1/traces"
)

metric_reader = PeriodicExportingMetricReader(exporter)

span_processor = BatchSpanProcessor(trace_exporter)

resource = Resource(
    attributes={
        SERVICE_NAME: "temporal-python",
    }
)

meterProvider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meterProvider)
meter = metrics.get_meter("temporal-python")

trace_provider = TracerProvider(resource=resource)
trace_provider.add_span_processor(span_processor)
trace.set_tracer_provider(trace_provider)  # Set the global tracer provider

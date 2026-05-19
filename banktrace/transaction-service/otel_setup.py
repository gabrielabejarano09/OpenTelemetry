import os
import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.requests import RequestsInstrumentor


def setup_otel(service_name: str):
    collector_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://localhost:4317"
    )

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: os.environ.get("SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.environ.get("DEPLOYMENT_ENV", "production"),
        "cloud.provider": "aws",
        "cloud.region": os.environ.get("AWS_REGION", "us-east-2"),
        "faas.name": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", service_name),
    })

    tracer_provider = TracerProvider(resource=resource)

    span_exporter = OTLPSpanExporter(
        endpoint=collector_endpoint,
        insecure=True,
        timeout=2
    )

    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            span_exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000,
            max_export_batch_size=512,
        )
    )

    trace.set_tracer_provider(tracer_provider)

    metric_exporter = OTLPMetricExporter(
        endpoint=collector_endpoint,
        insecure=True,
        timeout=2
    )

    reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=5000
    )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[reader]
    )

    metrics.set_meter_provider(meter_provider)

    logging.basicConfig(level=logging.INFO)

    RequestsInstrumentor().instrument()

    return trace.get_tracer(service_name), metrics.get_meter(service_name)
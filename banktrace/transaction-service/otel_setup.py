import os
import logging
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
 
def setup_otel(service_name: str):
    """
    Inicializa OpenTelemetry completo:
    - TracerProvider con exportador OTLP al Collector
    - MeterProvider con exportador OTLP (metricas cada 30s)
    - LoggerProvider con correlacion automatica de trace_id
    """
    collector_endpoint = os.environ.get(
        'OTEL_EXPORTER_OTLP_ENDPOINT',
        'http://otel-collector:4317'   # default para pruebas locales
    )
 
    # Recurso que identifica el servicio en New Relic
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: os.environ.get('SERVICE_VERSION', '1.0.0'),
        'deployment.environment': os.environ.get('DEPLOYMENT_ENV', 'production'),
        'cloud.provider': 'aws',
        'cloud.region': os.environ.get('AWS_REGION', 'us-east-1'),
        'faas.name': os.environ.get('AWS_LAMBDA_FUNCTION_NAME', service_name),
    })
 
    # ── TRAZAS ──
    tracer_provider = TracerProvider(resource=resource)
    span_exporter = OTLPSpanExporter(
        endpoint=collector_endpoint,
        insecure=True   # el Collector habla TLS con New Relic, no el servicio
    )
    # BatchSpanProcessor = ASINCRONO (no bloquea el request)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            span_exporter,
            max_queue_size=2048,
            schedule_delay_millis=5000,   # exporta cada 5s
            max_export_batch_size=512,
        )
    )
    trace.set_tracer_provider(tracer_provider)
 
    # ── METRICAS ──
    metric_exporter = OTLPMetricExporter(
        endpoint=collector_endpoint,
        insecure=True
    )
    reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=30_000   # envia metricas cada 30s
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
 
    # ── LOGS CORRELACIONADOS ──
    log_exporter = OTLPLogExporter(
        endpoint=collector_endpoint,
        insecure=True
    )
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(log_exporter)
    )
    # Inyecta trace_id y span_id en cada log automaticamente
    LoggingInstrumentor().instrument(set_logging_format=True)
    handler = LoggingHandler(
        level=logging.DEBUG,
        logger_provider=logger_provider
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
 
    # Instrumentacion automatica de requests HTTP (propaga contexto)
    RequestsInstrumentor().instrument()
 
    return trace.get_tracer(service_name), metrics.get_meter(service_name)

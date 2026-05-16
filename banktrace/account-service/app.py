# account-service/app.py
import json
import logging
import os
import time
import requests
from otel_setup import setup_otel
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.propagate import inject
 
# Inicializar OTel UNA vez al arrancar el contenedor Lambda (cold start)
tracer, meter = setup_otel('account-service')
logger = logging.getLogger('account-service')
 
# ── METRICAS CUSTOM ──
request_counter = meter.create_counter(
    name='banktrace.account.requests_total',
    description='Total de peticiones recibidas por account-service',
    unit='1'
)
order_latency = meter.create_histogram(
    name='banktrace.account.order_duration_ms',
    description='Latencia end-to-end de creacion de orden (ms)',
    unit='ms'
)
validation_errors = meter.create_counter(
    name='banktrace.account.validation_errors_total',
    description='Errores de validacion de cuenta',
    unit='1'
)
 
TRANSACTION_SERVICE_URL = os.environ.get(
    'TRANSACTION_SERVICE_URL',
    'https://XXXXXXXXXX.execute-api.us-east-1.amazonaws.com/prod'
)
 
 
def lambda_handler(event, context):
    """Punto de entrada Lambda — recibe eventos de API Gateway"""
    path = event.get('path', '/')
    method = event.get('httpMethod', 'GET')
    body = json.loads(event.get('body', '{}') or '{}')
 
    if path == '/api/v1/orders' and method == 'POST':
        return handle_create_order(body)
    elif path == '/health':
        return {'statusCode': 200, 'body': json.dumps({'status': 'ok', 'service': 'account-service'})}
    else:
        return {'statusCode': 404, 'body': json.dumps({'error': 'Not found'})}
 
 
def handle_create_order(body: dict) -> dict:
    """
    Flujo principal de creacion de orden.
    Genera el span raiz del trace — todos los spans hijos heredan el TraceId.
    """
    start_time = time.time()
    request_counter.add(1, {'endpoint': '/api/v1/orders', 'method': 'POST'})
 
    # Span raiz — aqui nace el TraceId
    with tracer.start_as_current_span(
        'POST /api/v1/orders',
        kind=SpanKind.SERVER
    ) as root_span:
        try:
            account_id = body.get('account_id')
            amount = body.get('amount', 0)
            order_type = body.get('type', 'TRANSFER')
 
            # Atributos del span — visibles en New Relic
            root_span.set_attribute('account.id', account_id or 'unknown')
            root_span.set_attribute('order.type', order_type)
            root_span.set_attribute('order.amount', float(amount))
            root_span.set_attribute('http.method', 'POST')
            root_span.set_attribute('http.route', '/api/v1/orders')
 
            logger.info(
                'Iniciando creacion de orden',
                extra={'account_id': account_id, 'amount': amount, 'order_type': order_type}
            )
 
            # Paso 1: Validar cuenta
            account_data = validate_account(account_id, amount)
 
            # Paso 2: Obtener historial de transacciones
            transactions = get_transaction_history(account_id)
 
            # Paso 3: Crear la orden
            order = create_order(account_id, amount, order_type, account_data)
 
            duration_ms = (time.time() - start_time) * 1000
            order_latency.record(duration_ms, {'order_type': order_type, 'status': 'success'})
 
            root_span.set_attribute('http.status_code', 200)
            root_span.set_status(StatusCode.OK)
 
            logger.info(
                'Orden creada exitosamente',
                extra={'order_id': order['order_id'], 'duration_ms': duration_ms}
            )
 
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps(order)
            }
 
        except ValueError as e:
            # Error de validacion — span se marca como ERROR
            root_span.set_status(StatusCode.ERROR, str(e))
            root_span.record_exception(e)
            validation_errors.add(1, {'reason': str(e)})
            logger.error('Error de validacion', extra={'error': str(e)})
            return {'statusCode': 400, 'body': json.dumps({'error': str(e)})}
 
        except Exception as e:
            root_span.set_status(StatusCode.ERROR, str(e))
            root_span.record_exception(e)
            logger.error('Error inesperado', extra={'error': str(e)})
            return {'statusCode': 500, 'body': json.dumps({'error': 'Internal error'})}
 
 
def validate_account(account_id: str, amount: float) -> dict:
    """Validacion de cuenta — span manual hijo del root span"""
    with tracer.start_as_current_span('validate_account') as span:
        span.set_attribute('account.id', account_id or 'unknown')
        span.set_attribute('validation.amount', float(amount))
 
        logger.info('Validando cuenta bancaria', extra={'account_id': account_id})
 
        if not account_id:
            span.set_status(StatusCode.ERROR, 'account_id requerido')
            raise ValueError('account_id es requerido')
 
        if amount <= 0:
            span.set_status(StatusCode.ERROR, 'monto invalido')
            raise ValueError(f'Monto invalido: {amount}')
 
        if amount > 50000:
            span.set_attribute('validation.high_value', True)
            logger.warning('Transaccion de alto valor detectada',
                           extra={'amount': amount, 'account_id': account_id})
 
        # Simulacion de consulta a base de datos de cuentas
        time.sleep(0.02)  # 20ms latencia simulada
 
        account_data = {
            'account_id': account_id,
            'balance': 100000.0,
            'status': 'ACTIVE',
            'owner': 'Cliente BankTrace'
        }
        span.set_attribute('account.status', account_data['status'])
        span.set_status(StatusCode.OK)
        return account_data
 
 
def get_transaction_history(account_id: str) -> list:
    """Llama a transaction-service — propaga el TraceId via W3C headers"""
    with tracer.start_as_current_span(
        'call_transaction_service',
        kind=SpanKind.CLIENT
    ) as span:
        span.set_attribute('peer.service', 'transaction-service')
        span.set_attribute('account.id', account_id)
        span.set_attribute('http.method', 'GET')
        span.set_attribute('http.url',
            f'{TRANSACTION_SERVICE_URL}/api/v1/transactions/{account_id}')
 
        # Inyectar TraceContext en los headers HTTP (propagacion de contexto)
        headers = {'Content-Type': 'application/json'}
        inject(headers)   # agrega traceparent y tracestate automaticamente
 
        logger.info('Llamando a transaction-service',
                    extra={'account_id': account_id})
 
        resp = requests.get(
            f'{TRANSACTION_SERVICE_URL}/api/v1/transactions/{account_id}',
            headers=headers,
            timeout=10
        )
 
        span.set_attribute('http.status_code', resp.status_code)
 
        if resp.status_code != 200:
            span.set_status(StatusCode.ERROR, f'HTTP {resp.status_code}')
            raise Exception(f'transaction-service error: {resp.status_code}')
 
        span.set_status(StatusCode.OK)
        data = resp.json()
        span.set_attribute('transactions.count', len(data.get('transactions', [])))
        return data.get('transactions', [])
 
 
def create_order(account_id: str, amount: float,
                  order_type: str, account_data: dict) -> dict:
    """Crea la orden en el sistema — span manual"""
    with tracer.start_as_current_span('create_order') as span:
        import uuid
        order_id = str(uuid.uuid4())
        span.set_attribute('order.id', order_id)
        span.set_attribute('order.type', order_type)
        span.set_attribute('order.amount', float(amount))
 
        time.sleep(0.01)  # 10ms simulando persistencia
 
        span.set_status(StatusCode.OK)
        logger.info('Orden persistida', extra={'order_id': order_id})
 
        return {
            'order_id': order_id,
            'account_id': account_id,
            'amount': amount,
            'type': order_type,
            'status': 'CREATED'
        }

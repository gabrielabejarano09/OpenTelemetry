import json
import logging
import os
import time
import boto3
from otel_setup import setup_otel
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.propagate import extract

tracer, meter = setup_otel('transaction-service')
logger = logging.getLogger('transaction-service')

dynamodb = boto3.resource(
    'dynamodb',
    region_name=os.environ.get('DYNAMODB_REGION', 'us-east-1')
)

TABLE_NAME = os.environ.get('TRANSACTIONS_TABLE', 'banktrace-transactions')

db_query_latency = meter.create_histogram(
    name='banktrace.transaction.db_query_duration_ms',
    description='Latencia de consultas a DynamoDB',
    unit='ms'
)

transactions_retrieved = meter.create_histogram(
    name='banktrace.transaction.records_count',
    description='Numero de registros retornados por consulta',
    unit='1'
)


def lambda_handler(event, context):
    path = event.get('path', '/')
    method = event.get('httpMethod', 'GET')
    headers = event.get('headers', {}) or {}

    if '/api/v1/transactions/' in path and method == 'GET':
        account_id = path.split('/')[-1]
        return handle_get_transactions(account_id, headers)

    if path == '/health':
        return {
            'statusCode': 200,
            'body': json.dumps({'status': 'ok', 'service': 'transaction-service'})
        }

    return {
        'statusCode': 404,
        'body': json.dumps({'error': 'Not found'})
    }


def handle_get_transactions(account_id: str, incoming_headers: dict) -> dict:
    ctx = extract(incoming_headers)

    with tracer.start_as_current_span(
        'GET /api/v1/transactions/{account_id}',
        context=ctx,
        kind=SpanKind.SERVER
    ) as span:
        try:
            span.set_attribute('account.id', account_id)
            span.set_attribute('http.method', 'GET')
            span.set_attribute('http.route', '/api/v1/transactions/{account_id}')
            span.set_attribute('peer.service', 'account-service')

            transactions = query_dynamodb(account_id)

            span.set_attribute('http.status_code', 200)
            span.set_attribute('transactions.returned', len(transactions))
            span.set_status(StatusCode.OK)

            logger.info(
                'Transacciones retornadas',
                extra={'account_id': account_id, 'count': len(transactions)}
            )

            trace.get_tracer_provider().force_flush()

            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'account_id': account_id,
                    'transactions': transactions
                })
            }

        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)

            logger.error(
                'Error consultando transacciones',
                extra={'account_id': account_id, 'error': str(e)}
            )

            trace.get_tracer_provider().force_flush()

            return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
            }


def query_dynamodb(account_id: str) -> list:
    with tracer.start_as_current_span('query_dynamo') as span:
        span.set_attribute('db.system', 'dynamodb')
        span.set_attribute('db.name', TABLE_NAME)
        span.set_attribute('db.operation', 'Query')
        span.set_attribute('db.dynamodb.table_name', TABLE_NAME)
        span.set_attribute('account.id', account_id)

        start = time.time()

        try:
            table = dynamodb.Table(TABLE_NAME)

            response = table.query(
                KeyConditionExpression='account_id = :aid',
                ExpressionAttributeValues={':aid': account_id},
                Limit=50,
                ScanIndexForward=False
            )

            items = response.get('Items', [])
            latency_ms = (time.time() - start) * 1000

            db_query_latency.record(latency_ms, {'operation': 'Query'})
            transactions_retrieved.record(len(items), {'account_id': account_id})

            span.set_attribute('db.dynamodb.count', len(items))
            span.set_attribute('db.query_duration_ms', latency_ms)
            span.set_status(StatusCode.OK)

            return items

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            db_query_latency.record(latency_ms, {'operation': 'Query', 'error': 'true'})

            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)

            raise
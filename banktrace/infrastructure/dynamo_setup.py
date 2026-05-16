import boto3
 
def create_table():
    client = boto3.client('dynamodb', region_name='us-east-1')
    client.create_table(
        TableName='banktrace-transactions',
        KeySchema=[
            {'AttributeName': 'account_id', 'KeyType': 'HASH'},
            {'AttributeName': 'timestamp',  'KeyType': 'RANGE'}
        ],
        AttributeDefinitions=[
            {'AttributeName': 'account_id', 'AttributeType': 'S'},
            {'AttributeName': 'timestamp',  'AttributeType': 'S'},
        ],
        BillingMode='PAY_PER_REQUEST'
    )
    print('Tabla banktrace-transactions creada exitosamente')
 
if __name__ == '__main__':
    create_table()
 
# Ejecutar:
# python infrastructure/dynamo_setup.py

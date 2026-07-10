# session_model.py

from flask import redirect,url_for, jsonify, current_app, session

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import BotoCoreError, ClientError
from decimal import Decimal
import json

from renglo.dynamodb_resource import get_dynamodb_resource

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)

class SessionModel:

    def __init__(self, config=None, tid=False, ip=False):
        """
        Initialize SessionModel with configuration.
        
        Args:
            config (dict): Configuration dictionary containing self.DYNAMODB_SESSION_TABLE
            tid: Transaction ID (optional)
            ip: IP address (optional)
        """
        self.config = config or {}
        
        region = self.config.get('AWS_REGION', 'us-east-1')
        self.dynamodb = get_dynamodb_resource(region)
        
        table_name = self.config.get('DYNAMODB_SESSION_TABLE', 'default_session_table')
        self.session_table = self.dynamodb.Table(table_name)
        self.DYNAMODB_SESSION_TABLE = table_name

    def sanitize(self, obj):
        if isinstance(obj, list):
            return [self.sanitize(x) for x in obj]
        elif isinstance(obj, dict):
            return {k: self.sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, Decimal):
            # Convert Decimal to int if it's a whole number, otherwise float
            return int(obj) if obj % 1 == 0 else float(obj)
        elif isinstance(obj, (int, float)):
            # Keep numbers as is - DynamoDB will handle them
            return obj
        else:
            return obj
        
        
    
    def query_session(self,index,entity_index_prefix,limit=50,lastkey=None,sort='asc'):
        
        result = {}
        all_items = []

        try:
            while True:
                # Build the query parameters with KeyConditionExpression

                if entity_index_prefix:

                    query_params = {
                        'KeyConditionExpression': Key('index').eq(index) & Key('entity_index').begins_with(entity_index_prefix),
                        'Limit': limit,
                        "ScanIndexForward": True if sort == 'asc' else False
                    }
                                     
                else:
                    # Handle case when no prefix is provided
                    query_params = {
                        'KeyConditionExpression': Key('index').eq(index),  # Only query by index
                        'Limit': limit,
                        "ScanIndexForward": True if sort == 'asc' else False
                    }
                
       

                # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
                if lastkey:
                    query_params['ExclusiveStartKey'] = lastkey

                
                response = self.session_table.query(**query_params)
                
                
                # Extract items and pagination key
                items = response.get('Items', [])
                all_items.extend(items)  # Add current page items to the complete list
                
                # Get the pagination key for next query
                lastkey = response.get('LastEvaluatedKey')
                
                # If there's no more pages, break the loop
                if not lastkey:
                    break

            # Build the result with all items
            result['success'] = True
            result['items'] = all_items
            
            return result

        except (BotoCoreError, ClientError) as e:
            
            result['success'] = False
            result['message'] = 'Items could not be retrieved'
            result['items'] = all_items
            result['error'] = str(e)
            status = 400
            return result
        
        

    def list_session(self,index,entity_index,limit=50,lastkey=None,sort='asc'):
        
        result = {}
        all_items = []

        try:
            while True:
                # Build the query parameters with KeyConditionExpression using LSI
                query_params = {
                    'KeyConditionExpression': Key('index').eq(index) & Key('entity_index').eq(entity_index),
                    'Limit': limit,
                    "ScanIndexForward": True if sort == 'asc' else False
                }
                           

                # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
                if lastkey:
                    query_params['ExclusiveStartKey'] = lastkey

                # Query DynamoDB to get items with matching PK and SK prefix
                response = self.session_table.query(**query_params)
                
                # Extract items and pagination key
                items = response.get('Items', [])
                all_items.extend(items)  # Add current page items to the complete list
                
                # Get the pagination key for next query
                lastkey = response.get('LastEvaluatedKey')
                
                # If there's no more pages, break the loop
                if not lastkey:
                    break

            # Build the result with all items
            result['success'] = True
            result['items'] = all_items
            
            return result

        except (BotoCoreError, ClientError) as e:
            
            result['success'] = False
            result['message'] = 'Items could not be retrieved'
            result['items'] = all_items
            result['error'] = str(e)
            status = 400
            return result
        
    
    def get_session(self,index,entity_index,message_id):
        
        result = {}
        
        current_app.logger.debug(f'get_session: {index} > {message_id}')
        
        try:
            # Build the query parameters with KeyConditionExpression

            
            query_params = {
                'KeyConditionExpression': Key('index').eq(index) & Key('entity_index').eq(entity_index),
                'FilterExpression': Attr('_id').eq(message_id)
            }
            


            current_app.logger.debug(f'Query parameters: {query_params}')

            # Query DynamoDB to get the specific item
            response = self.session_table.query(**query_params)
            #current_app.logger.debug(f'Raw DynamoDB response: {response}')
            
            # Extract items
            items = response.get('Items', [])
            #current_app.logger.debug(f'Extracted items: {items}')
            
            if not items:
                current_app.logger.debug(f'No items found for index: {index} and message_id: {message_id}')
                result['success'] = False
                result['message'] = 'Item not found'
                return result
            
            #print(f'CHM:get_session > {items[0]}')
            
            # Build the result
            result['success'] = True
            result['item'] = items[0]  # Return single item
            
            return result

        except Exception as e:
            current_app.logger.error(f"Error in get_session: {str(e)}")
            result['success'] = False
            result['message'] = 'Item could not be retrieved'
            result['error'] = str(e)
            return result
        
        
        
    def create_session(self,data):
        
        print(f'create_session > input:{data}')

        try:
            # Sanitize data before storing
            sanitized_data = self.sanitize(data)
            response = self.session_table.put_item(Item=sanitized_data)
            current_app.logger.debug('MODEL: Created session successfully:'+str(sanitized_data))
            return {
                "success":True, 
                "message": "Session created", 
                "document": sanitized_data,
                "status" : response['ResponseMetadata']['HTTPStatusCode']
                }
        except ClientError as e:
            print(f'create_session > error:{e}')
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "document": data,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }
        
        
        
    def update_session(self,data):


        try:
            # Sanitize data before storing
            sanitized_data = self.sanitize(data)
            response = self.session_table.put_item(Item=sanitized_data)
            #current_app.logger.debug('MODEL: Updated entity successfully')
            return {
                "success":True, 
                "message": "Session updated", 
                "document": sanitized_data,
                "status" : response['ResponseMetadata']['HTTPStatusCode']
                }
        except ClientError as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "document": data,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }

      
    # NOT USED  
    def delete_session(self,**data):

        keys = {
            'irn': data['irn'],
            'time': data['time']
        }

        try:
            response = self.session_table.delete_item(Key=keys)
            current_app.logger.debug('MODEL: Deleted Session:' + str(data))
            return {
                "success":True,
                "message": "Entity deleted", 
                "document": data,
                "status" : response['ResponseMetadata']['HTTPStatusCode'] 
                }
        
        except ClientError as e:
            return {
                "success":False,
                "message": e.response['Error']['Message'],
                "document": data,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }

            
    
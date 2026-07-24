# chat_model.py

from flask import redirect,url_for, jsonify, current_app, session

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import BotoCoreError, ClientError
from decimal import Decimal
import json
import logging

from renglo.dynamodb_resource import get_dynamodb_resource

_logger_workspace = logging.getLogger("agent.workspace")

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)

class ChatModel:

    def __init__(self, config=None, tid=False, ip=False):
        """
        Initialize ChatModel with configuration.

        Args:
            config (dict): Configuration dictionary containing self.DYNAMODB_CHAT_TABLE
            tid: Transaction ID (optional)
            ip: IP address (optional)
        """
        self.config = config or {}

        self.dynamodb = get_dynamodb_resource("us-east-1")

        table_name = self.config.get('DYNAMODB_CHAT_TABLE', 'default_chat_table')
        self.chat_table = self.dynamodb.Table(table_name)
        self.DYNAMODB_CHAT_TABLE = table_name

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



    def query_chat(self,index,entity_index_prefix,limit=50,lastkey=None,sort='asc'):

        result = {}
        all_items = []

        try:
            # DynamoDB Limit is per request, not total; paginate with LastEvaluatedKey. Cap per page (API max 1000).
            page_limit = max(1, min(int(limit), 1000))
            exclusive_start_key = lastkey
            max_pages = 500
            page = 0
            while page < max_pages:
                page += 1
                # Build the query parameters with KeyConditionExpression

                if entity_index_prefix:

                    query_params = {
                        'TableName': self.DYNAMODB_CHAT_TABLE,
                        'KeyConditionExpression': Key('index').eq(index) & Key('entity_index').begins_with(entity_index_prefix),
                        'Limit': page_limit,
                        "ScanIndexForward": True if sort == 'asc' else False
                    }

                else:
                    # Handle case when no prefix is provided
                    query_params = {
                        'TableName': self.DYNAMODB_CHAT_TABLE,
                        'KeyConditionExpression': Key('index').eq(index),  # Only query by index
                        'Limit': page_limit,
                        "ScanIndexForward": True if sort == 'asc' else False
                    }



                if exclusive_start_key:
                    query_params['ExclusiveStartKey'] = exclusive_start_key


                response = self.chat_table.query(**query_params)


                # Extract items and pagination key
                items = response.get('Items', [])
                all_items.extend(items)  # Add current page items to the complete list

                exclusive_start_key = response.get('LastEvaluatedKey')

                if not exclusive_start_key:
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



    def list_chat(self,index,entity_index,limit=50,lastkey=None,sort='asc'):

        result = {}
        all_items = []

        try:
            while True:
                # Build the query parameters with KeyConditionExpression using LSI
                query_params = {
                    'TableName': self.DYNAMODB_CHAT_TABLE,
                    'KeyConditionExpression': Key('index').eq(index) & Key('entity_index').eq(entity_index),
                    'Limit': limit,
                    "ScanIndexForward": True if sort == 'asc' else False
                }


                # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
                if lastkey:
                    query_params['ExclusiveStartKey'] = lastkey

                # Query DynamoDB to get items with matching PK and SK prefix
                response = self.chat_table.query(**query_params)

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


    def get_chat(self,index,entity_index,message_id):

        result = {}

        current_app.logger.debug(f'get_chat: {index} > {message_id}')

        try:
            # Build the query parameters with KeyConditionExpression


            query_params = {
                'TableName': self.DYNAMODB_CHAT_TABLE,
                'KeyConditionExpression': Key('index').eq(index) & Key('entity_index').eq(entity_index),
                'FilterExpression': Attr('_id').eq(message_id)
            }



            current_app.logger.debug(f'Query parameters: {query_params}')

            # Query DynamoDB to get the specific item
            response = self.chat_table.query(**query_params)
            #current_app.logger.debug(f'Raw DynamoDB response: {response}')

            # Extract items
            items = response.get('Items', [])
            #current_app.logger.debug(f'Extracted items: {items}')

            if not items:
                current_app.logger.debug(f'No items found for index: {index} and message_id: {message_id}')
                result['success'] = False
                result['message'] = 'Item not found'
                return result

            #print(f'CHM:get_chat > {items[0]}')

            # Build the result
            result['success'] = True
            result['item'] = items[0]  # Return single item

            return result

        except Exception as e:
            current_app.logger.error(f"Error in get_chat: {str(e)}")
            result['success'] = False
            result['message'] = 'Item could not be retrieved'
            result['error'] = str(e)
            return result



    def create_chat(self,data):


        try:
            # Sanitize data before storing
            sanitized_data = self.sanitize(data)
            response = self.chat_table.put_item(Item=sanitized_data)
            current_app.logger.debug('MODEL: Created chat successfully:'+str(sanitized_data))
            return {
                "success":True,
                "message": "Chat created",
                "document": sanitized_data,
                "status" : response['ResponseMetadata']['HTTPStatusCode']
                }
        except ClientError as e:
            _logger_workspace.error("create_chat_failed | %s", e)
            return {
                "success":False,
                "message": e.response['Error']['Message'],
                "document": data,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }



    def update_chat(self,data):


        try:
            # Sanitize data before storing
            sanitized_data = self.sanitize(data)
            response = self.chat_table.put_item(Item=sanitized_data)
            #current_app.logger.debug('MODEL: Updated entity successfully')
            return {
                "success":True,
                "message": "Chat updated",
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


    def delete_chat(self, data):
        """Delete one chat row by its DynamoDB primary key (index + entity_index)."""
        try:
            keys = {
                'index': data['index'],
                'entity_index': data['entity_index'],
            }
            response = self.chat_table.delete_item(Key=keys)
            current_app.logger.debug('MODEL: Deleted Chat: %s', keys)
            return {
                "success": True,
                "message": "Entity deleted",
                "document": data,
                "status": response['ResponseMetadata']['HTTPStatusCode'],
            }
        except ClientError as e:
            return {
                "success": False,
                "message": e.response['Error']['Message'],
                "document": data,
                "status": e.response['ResponseMetadata']['HTTPStatusCode'],
            }
        except (KeyError, TypeError) as e:
            return {
                "success": False,
                "message": f"Missing delete keys: {e}",
                "document": data,
                "status": 400,
            }

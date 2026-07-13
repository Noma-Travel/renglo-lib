#data_model.py

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime
from flask import current_app, jsonify
from renglo.auth.auth_controller import AuthController
from renglo.blueprint.blueprint_controller import BlueprintController
from renglo.dynamodb_resource import get_dynamodb_resource


class DataModel:

    def __init__(self, config=None, tid=False, ip=False):
        self.config = config or {}

        self.AUC = AuthController(config=self.config, tid=tid, ip=ip)
        self.BPC = BlueprintController(config=self.config, tid=tid, ip=ip)

        self.dynamodb = get_dynamodb_resource("us-east-1")
        table_name = self.config.get('DYNAMODB_RINGDATA_TABLE')
        if not table_name:
            raise ValueError("DYNAMODB_RINGDATA_TABLE configuration is required but not found")
        self.data_table = self.dynamodb.Table(table_name)
        self.DYNAMODB_RINGDATA_TABLE = table_name



    def post_a_b(self,portfolio,org,ring,item):



        item['portfolio_index'] = 'irn:data:'+portfolio
        item['doc_index'] = org+':'+ring+':'+item['_id'] # _id was generated in the controller

        try:
            self.data_table.put_item(Item=item)
            #self.increase_item_count(handle,ring)  #TO-DO: Implement increase_item_count!
            return {"message": "Item created", "document": item}
        except ClientError as e:
            return {"error": e.response['Error']['Message']}




    def get_a_b(self, portfolio, org, ring, limit=10000, lastkey=None):
        # Construct the partition key and sort key prefix
        portfolio_index = f'irn:data:{portfolio}'  # This will be used as the partition key (PK)

        # Sort key (SK) prefix for querying documents based on org, ring, and a generated prefix
        prefix_doc_index = f'{org}:{ring}'  # This is the prefix we will use in begins_with for SK
        try:
            # Build the query parameters with KeyConditionExpression
            query_params = {
                'TableName': self.DYNAMODB_RINGDATA_TABLE,
                'KeyConditionExpression': Key('portfolio_index').eq(portfolio_index) & Key('doc_index').begins_with(prefix_doc_index),
                'Limit': limit
            }
            # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
            if lastkey:
                query_params['ExclusiveStartKey'] = {
                        'doc_index': f'{org}:{ring}:{lastkey}',
                        'portfolio_index': f'irn:data:{portfolio}'
                    }

            # Query DynamoDB to get items with matching PK and SK prefix
            response = self.data_table.query(**query_params)

            # Extract items and pagination key
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey')  # Pagination key for next query

            # Build the result
            result ={}
            result['items'] = items
            if endkey:
                result['last_id'] = endkey['doc_index'].split(':')[-1]
            else:
                result['last_id'] = None

            return result

        except (BotoCoreError, ClientError) as e:
            return {"error": str(e)}



    def get_a_b_batch(self, portfolio, org, ring, limit=10000, lastkey=None):
        # Construct the partition key and sort key prefix
        portfolio_index = f'irn:data:{portfolio}'  # This will be used as the partition key (PK)

        # Sort key (SK) prefix for querying documents based on org, ring, and a generated prefix
        prefix_doc_index = f'{org}:{ring}'  # This is the prefix we will use in begins_with for SK

        try:
            # Build the query parameters with KeyConditionExpression
            query_params = {
                'TableName': self.DYNAMODB_RINGDATA_TABLE,
                'KeyConditionExpression': Key('portfolio_index').eq(portfolio_index) & Key('doc_index').begins_with(prefix_doc_index),
                'Limit': limit
            }

            # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
            if lastkey:
                query_params['ExclusiveStartKey'] = {
                        'doc_index': f'{org}:{ring}:{lastkey}',
                        'portfolio_index': f'irn:data:{portfolio}'
                    }

            # Query DynamoDB to get items with matching PK and SK prefix
            response = self.data_table.query(**query_params)

            # Extract items and pagination key
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey')  # Pagination key for next query

            # Build the result
            result ={}
            result['items'] = items
            if endkey:
                result['last_id'] = endkey['doc_index'].split(':')[-1]
            else:
                result['last_id'] = None

            return result

        except (BotoCoreError, ClientError) as e:
            return {"error": str(e)}






    #Deprecated
    def get_a_index(self, portfolio, prefix_path, lastkey=None):
        # Construct the partition key and sort key prefix
        portfolio_index = f'irn:data:{portfolio}'  # This will be used as the partition key (PK)
        path_index = f'{prefix_path}'

        try:
            # Build the query parameters with KeyConditionExpression
            query_params = {
                'TableName': self.DYNAMODB_RINGDATA_TABLE,  # Make sure this references the correct DynamoDB table
                'IndexName': 'path_index',  # Specify the name of your secondary index
                'KeyConditionExpression': Key('portfolio_index').eq(portfolio_index) & Key('path_index').begins_with(path_index),  # Include both keys
            }

            # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
            if lastkey:
                query_params['ExclusiveStartKey'] = lastkey  # Ensure lastkey has both PK and SK components

            # Query DynamoDB to get items with matching PK and SK prefix
            response = self.data_table.query(**query_params)

            # Extract items and pagination key
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey')  # Pagination key for next query

            # Build the result
            result = {
                'items': items,
                'lastkey': endkey  # This will be passed as 'lastkey' in the next call if needed
            }

            return result

        except (BotoCoreError, ClientError) as e:
            return {"error": str(e)}

    #Deprecated
    def get_a_b_index(self, portfolio, prefix_path, lastkey=None):
        # Construct the partition key and sort key prefix
        portfolio_index = f'irn:data:{portfolio}'  # This will be used as the partition key (PK)
        path_index = f'{prefix_path}'

        try:
            # Build the query parameters with KeyConditionExpression
            query_params = {
                'TableName': self.DYNAMODB_RINGDATA_TABLE,  # Make sure this references the correct DynamoDB table
                'IndexName': 'path_index',  # Specify the name of your secondary index
                'KeyConditionExpression': Key('portfolio_index').eq(portfolio_index) & Key('path_index').begins_with(path_index),  # Include both keys
            }

            # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
            if lastkey:
                query_params['ExclusiveStartKey'] = lastkey  # Ensure lastkey has both PK and SK components

            # Query DynamoDB to get items with matching PK and SK prefix
            response = self.data_table.query(**query_params)

            # Extract items and pagination key
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey')  # Pagination key for next query

            # Build the result
            result = {
                'items': items,
                'lastkey': endkey  # This will be passed as 'lastkey' in the next call if needed
            }

            return result

        except (BotoCoreError, ClientError) as e:
            return {"error": str(e)}



    def get_a_b_c(self,portfolio,org,ring,idx):

        #irn = 'irn:data:'+portfolio+':'+org+':'+ring+':*'
        portfolio_index = 'irn:data:'+portfolio
        doc_index = org+':'+ring+':'+idx # _id was generated in the controller


        try:

            response = self.data_table.get_item(Key={'portfolio_index': portfolio_index, 'doc_index': doc_index})
            item = response.get('Item')

            if item:
                return item
            else:
                return {"error": "Document not found"}
        except ClientError as e:
            return {"error": e.response['Error']['Message']}




    def put_a_b_c(self, portfolio, org, ring, idx, item):
        # Construct the new primary key (partition key) and sort key

        # Set the partition and sort keys in the item
        portfolio_index = 'irn:data:'+portfolio
        doc_index = org+':'+ring+':'+item['_id']


        new_item = item.get('attributes', {})  # Retrieve the new attributes to update

        if not all([portfolio_index, doc_index, new_item]):
            return {'error': 'Missing portfolio_index, doc_index, or items'}

        try:
            # Fetch the current item based on the new partition key and sort key
            response_1 = self.data_table.get_item(Key={'portfolio_index': portfolio_index, 'doc_index': doc_index})
            if 'Item' not in response_1:
                return {'error': 'Item not found'}

            current_items = response_1['Item'].get('attributes', {})  # Retrieve the current attributes

            # Build the update expression for attributes
            update_expression = "set "  # This will hold the DynamoDB update expression
            expression_attribute_values = {}
            expression_attribute_names = {}
            updates = []

            # Compare new values with current values and build update expression
            for key, new_value in new_item.items():
                current_value = current_items.get(key)
                if new_value != current_value:
                    updates.append(f"attributes.#key{key} = :value{key}")
                    expression_attribute_values[f":value{key}"] = new_value
                    expression_attribute_names[f"#key{key}"] = key

            if not updates:
                return {'message': 'No changes detected'}

            # Add a timestamp for the "modified" field
            timestamp = datetime.now().isoformat()
            updates.append("modified = :modified")
            expression_attribute_values[":modified"] = timestamp

            # Combine the updates into a single update expression
            update_expression += ", ".join(updates)

            # Update the item in DynamoDB
            response_2 = self.data_table.update_item(
                Key={'portfolio_index': portfolio_index, 'doc_index': doc_index},  # New partition and sort keys
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_attribute_values,
                ExpressionAttributeNames=expression_attribute_names,
                ReturnValues="UPDATED_NEW"
            )

            return {'message': 'Item updated', 'response': str(response_2['Attributes'])}

        except ClientError as e:
            return {'error': str(e)}



    def delete_a_b_c(self,portfolio,org,ring,idx):

        #irn = 'irn:data:'+portfolio+':'+org+':'+ring+':*'
        portfolio_index = 'irn:data:'+portfolio
        doc_index = org+':'+ring+':'+idx # _id was generated in the controller


        try:
            response = self.data_table.delete_item(Key={'portfolio_index': portfolio_index, 'doc_index': doc_index})
            return {'message':'Item deleted', 'response':str(response)}
        except ClientError as e:
            return {'error': str(e)}



    #INDEX QUERIES

    def get_a_b_beginswith(self,query):

        '''
        Incoming object instance
            {
            'portfolio':<portfolio_id>,
            'org':<org_id>,
            'ring':<ring_id>,
            'operator':'begins_with',
            'value':<value>,
            'filter':{
                   'operator':<greater_than|less_than>,
                   'field':<field_to_filter_on>,
                   'value':<value_filter_uses_on_the_field>
                },
            'limit':<page_limit>,
            'lastkey':<page_lastkey>,
            'sort': <asc|desc>
            }
        '''


        portfolio_index = f'irn:data:{query["portfolio"]}'  # This will be used as the partition key (PK)
        path_index_head = f'irn:h_index:{query["org"]}:{query["ring"]}'
        if 'value' not in query or not query['value']:
            path_index_tail = ''
        else:
            path_index_tail = f':{query["value"]}'
        path_index = path_index_head + path_index_tail


        try:
            # Build the query parameters with KeyConditionExpression
            query_params = {
                'TableName': self.DYNAMODB_RINGDATA_TABLE,  # Make sure this references the correct DynamoDB table
                'IndexName': 'path_index',  # Specify the name of your secondary index
                'KeyConditionExpression': Key('portfolio_index').eq(portfolio_index) & Key('path_index').begins_with(path_index),  # Include both keys
                'Limit': query['limit'],
                "ScanIndexForward": False if query['sort'] == 'asc' else True
            }
            # Add the ExclusiveStartKey to the query parameters if provided (for pagination)
            if query['lastkey']:
                query_params['ExclusiveStartKey'] = query['lastkey']

            # Query DynamoDB to get items with matching PK and SK prefix
            response = self.data_table.query(**query_params)

            # Extract items and pagination key
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey')  # Pagination key for next query

            # Build the result
            result = {
                'items': items,
                'lastkey': endkey  # This will be passed as 'lastkey' in the next call if needed
            }

            return result

        except (BotoCoreError, ClientError) as e:
            return {"error": str(e)}


    def get_a_b_greaterthan(self,portfolio_index,path_index):

        return {'message': 'This feature is not implemented yet'}

    def get_a_b_lessthan(self,portfolio_index,path_index):

        return {'message': 'This feature is not implemented yet'}

    def get_a_b_equalto(self,portfolio_index,path_index):

        return {'message': 'This feature is not implemented yet'}

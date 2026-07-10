from flask import redirect,url_for, jsonify, current_app, session, request

import boto3
from botocore.exceptions import ClientError
from datetime import datetime
import uuid
from decimal import Decimal

from renglo.dynamodb_resource import get_dynamodb_resource


class BlueprintModel:

    def __init__(self, config=None, tid=False, ip=False):
        self.config = config or {}
        self.dynamodb = get_dynamodb_resource("us-east-1")
        table_name = self.config.get('DYNAMODB_BLUEPRINT_TABLE', 'default_blueprint_table')
        self.blueprints_table = self.dynamodb.Table(table_name)
            

    
    def put_blueprint(self,data):

        try:
            self.blueprints_table.put_item(Item=data)
            return jsonify({"message": "Document created", "document": data}), 201
        except ClientError as e:
            return jsonify({"error": e.response['Error']['Message']}), 500


    def get_blueprint(self,handle,name,v):

        irn = 'irn:blueprint:' + handle +':'+ name

        current_app.logger.debug('Get Blueprint '+irn+' v:'+v)
        

        try:
            if v == 'last':
                response = self.blueprints_table.query(
                    KeyConditionExpression=boto3.dynamodb.conditions.Key('irn').eq(irn),
                    ScanIndexForward=False # Show latest blueprint versions first
                )
                items = response.get('Items', [])
                
                if len(items)==0:
                    return {"success":False,"message": "Document not found"}
                item = items[0]
                #current_app.logger.info('items from DB:'+str(items))
                       
            else:
                response = self.blueprints_table.get_item(Key={'irn': irn, 'version': v})        
                item = response.get('Item')

            if item:
                return item
            else:
                return {"success":False,"message": "Document not found"}
        except ClientError as e:
            return {"error": e.response['Error']['Message']}
        

    def update_blueprint(self,data):

        try:
            self.blueprints_table.put_item(Item=data)
            return jsonify({"message": "Document updated", "document": data})
        except ClientError as e:
            return jsonify({"error": e.response['Error']['Message']}), 500
        
    
    def delete_blueprint(self,handle,name,v):
        
        pk = 'irn:blueprint:' + handle +':'+ name
        sk = v
        
        try:
            self.blueprints_table.delete_item(Key={'irn': pk, 'version': sk})
            return jsonify({"message": "Document deleted"})
        except ClientError as e:
            return jsonify({"error": e.response['Error']['Message']}), 500


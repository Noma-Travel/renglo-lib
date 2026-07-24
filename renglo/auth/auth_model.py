import logging
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
import uuid
from decimal import Decimal
import requests

from renglo.dynamodb_resource import get_dynamodb_resource

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10


class AuthModel:

    def __init__(self, config=None, tid=False, ip=False):
        self.config = config or {}
        
        #Dynamo
        self.dynamodb = get_dynamodb_resource()
        entity_table_name = self.config.get('DYNAMODB_ENTITY_TABLE', 'default_entity_table')
        rel_table_name = self.config.get('DYNAMODB_REL_TABLE', 'default_rel_table')
        self.entity_table = self.dynamodb.Table(entity_table_name)
        self.rel_table = self.dynamodb.Table(rel_table_name)

        #SES
        cognito_region = self.config.get('COGNITO_REGION', 'us-east-1')
        self.cognito_client = boto3.client('cognito-idp', region_name=cognito_region) 
        self.USER_POOL_ID = self.config.get('COGNITO_USERPOOL_ID', '')
        self.COGNITO_APP_CLIENT_ID = self.config.get('COGNITO_APP_CLIENT_ID', '')


 #-------------------------------------------------AWS COGNITO


    def check_user_by_email(self,email):
        try:
            # Get the email from the request
            #email = request.json.get('email')
            if not email:
                return {'success': False, 'error': 'Email is required', 'status': 400}

            # List users by email filter
            response = self.cognito_client.list_users(
                UserPoolId=self.USER_POOL_ID,
                Filter=f'email = "{email}"'  # Filter by email
            )

            # Check if a user was found
            if response['Users']:
                user = response['Users'][0]  # Get the first user from the response

                # Extract Cognito User ID (the 'sub' attribute)
                cognito_user_id = next(
                    (attr['Value'] for attr in user['Attributes'] if attr['Name'] == 'sub'), 
                    None
                )

                if cognito_user_id:
                    return {
                        "success":True, 
                        "message": "User found", 
                        "document": {'email':email,'cognito_user_id':cognito_user_id},
                        "status" : 200
                    } 
                        
            return {
                "success":False, 
                "message": "User not found",
                "status" : 404
            }

        except self.cognito_client.exceptions.UserNotFoundException:
            return {
                "success":False, 
                "message": "User not found (UserNotFoundException)",
                "status" : 404
            }
        except Exception as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
            }
        
    #DEPRECATED
    def cognito_user_create_with_permanent_password(self,email, password,first='FIRST',last='LAST'):
        try:
            # Step 1: Create the user with a temporary password
            response_1 = self.cognito_client.admin_create_user(
                UserPoolId=self.USER_POOL_ID,
                Username=email,
                UserAttributes=[
                    {'Name': 'email', 'Value': email},
                    {'Name': 'email_verified', 'Value': 'true'},
                    {'Name': 'given_name', 'Value': first },
                    {'Name': 'family_name','Value': last }
                ],
                TemporaryPassword=password,
                MessageAction='SUPPRESS'  # Optionally suppress the email notification
            )

            
            # Step 2: Set the password as permanent
            response_2 = self.cognito_client.admin_set_user_password(
                UserPoolId=self.USER_POOL_ID,
                Username=email,
                Password=password,
                Permanent=True  # Make the password permanent
            )

            print(f"User {email} created with a permanent password.")

        except Exception as e:
            print(f"Error creating user: {str(e)}")


    
    def cognito_user_permanent_password_assign(self,email,password):
        try:
            
            # Set the password as permanent
            response = self.cognito_client.admin_set_user_password(
                UserPoolId=self.USER_POOL_ID,
                Username=email,
                Password=password,
                Permanent=True  # Make the password permanent
            )

            print(f"User {email} created with a permanent password.")
            # Return success message
            return {
                'success': True,
                'message': 'Password assigned',
                'document': response,
                'status': 200
            }

        except Exception as e:
            return {
                'success': False,
                'message': str(e),
                'status': 400
            }
        


    def cognito_user_create(self,email,first='FIRST',last='LAST'):
        try:
            
            temporary_password = 'TempPassword123!'
            # Create the user in the Cognito User Pool
            response = self.cognito_client.admin_create_user(
                UserPoolId=self.USER_POOL_ID,
                Username=email,
                UserAttributes=[
                    {
                        'Name': 'email',
                        'Value': email
                    },
                    {
                        'Name': 'email_verified',
                        'Value': 'true'
                    },
                    {
                        'Name': 'given_name',
                        'Value': first
                    },
                    {
                        'Name': 'family_name',
                        'Value': last
                    }
                ],
                TemporaryPassword=temporary_password,  # Optional: Set a temporary password for the user
                MessageAction='SUPPRESS'  # Optional: Suppresses the sending of the welcome email
            )

            # Return success message
            return {
                'success': True,
                'message': 'User created successfully',
                'document': response,
                'status': 200
            }

        except Exception as e:
            return {
                'success': False,
                'message': str(e),
                'status': 400
            }

    def cognito_user_delete(self, email):
        """
        Delete Cognito user(s) matching the email attribute.
        Needed so People-page deletes can fully free an address for re-invite.
        """
        try:
            if not email:
                return {
                    'success': False,
                    'message': 'Email is required',
                    'status': 400,
                }

            response = self.cognito_client.list_users(
                UserPoolId=self.USER_POOL_ID,
                Filter=f'email = "{email}"',
            )
            users = response.get('Users') or []
            if not users:
                return {
                    'success': True,
                    'message': 'No Cognito user for this email',
                    'document': {'deleted': []},
                    'status': 200,
                }

            deleted = []
            for user in users:
                username = user.get('Username')
                if not username:
                    continue
                self.cognito_client.admin_delete_user(
                    UserPoolId=self.USER_POOL_ID,
                    Username=username,
                )
                deleted.append(username)

            return {
                'success': True,
                'message': f'Deleted {len(deleted)} Cognito user(s)',
                'document': {'deleted': deleted},
                'status': 200,
            }
        except Exception as e:
            return {
                'success': False,
                'message': str(e),
                'status': 400,
            }


    #NOT USED 
    def cognito_user_login_challenge(self,email,new_password):

        temporary_password = 'TempPassword123!'
        
        try:
            # Step 1: Authenticate the user with the email and temporary password
            auth_response = self.cognito_client.admin_initiate_auth(
                UserPoolId=self.USER_POOL_ID,
                ClientId=self.COGNITO_APP_CLIENT_ID,
                AuthFlow='ADMIN_NO_SRP_AUTH',
                AuthParameters={
                    'USERNAME': email,  # Use email as the username
                    'PASSWORD': temporary_password
                }
            )

            # Step 2: Check if a password change is required
            if auth_response['ChallengeName'] == 'NEW_PASSWORD_REQUIRED':
                # Step 3: Respond to the password challenge by providing the new password
                challenge_response = self.cognito_client.respond_to_auth_challenge(
                    ClientId=self.COGNITO_APP_CLIENT_ID,
                    ChallengeName='NEW_PASSWORD_REQUIRED',
                    ChallengeResponses={
                        'USERNAME': email,  # Use email as the username
                        'NEW_PASSWORD': new_password,
                        'PASSWORD': temporary_password
                    },
                    Session=auth_response['Session']
                )
                return {
                    'success': True,
                    'message': 'Password changed successfully. User is now authenticated.',
                    'document': challenge_response['AuthenticationResult'],
                    'status': 200
                }

            else:
                return {
                    'success': False,
                    'message': 'Unexpected challenge. Expected NEW_PASSWORD_REQUIRED.',
                    'status': 400
                }

        except self.cognito_client.exceptions.NotAuthorizedException:
            return {'success': False, 'message': 'Invalid temporary password', 'status':401}
        except Exception as e:
            return {'success': False, 'message': str(e),'status':500}

            





#---------------------------------------------------- RESEND (transactional email)



    def send_email(self, sender, recipient, subject, body_text, body_html):
        """
        Send a transactional email via Resend (https://resend.com).

        Requires RESEND_API_KEY in config. The `sender` must come from a domain
        verified in the Resend account; otherwise Resend returns 422.

        Returns the same envelope shape the rest of the codebase expects:
            {success: bool, message: str, document: dict, status: int}
        """
        api_key = (self.config.get('RESEND_API_KEY') or '').strip()
        if not api_key:
            return {
                "success": False,
                "message": "RESEND_API_KEY is not configured",
                "document": {"error": "missing_api_key"},
                "status": 500,
            }

        payload = {
            "from": sender,
            "to": [recipient] if isinstance(recipient, str) else list(recipient),
            "subject": subject,
            "html": body_html,
            "text": body_text,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                RESEND_API_URL,
                json=payload,
                headers=headers,
                timeout=RESEND_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            return {
                "success": False,
                "message": f"Resend request failed: {exc}",
                "document": {"error": "network_error"},
                "status": 502,
            }

        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}

        if 200 <= response.status_code < 300:
            return {
                "success": True,
                "message": "Email sent",
                "document": {"MessageId": data.get("id"), "provider": "resend"},
                "status": response.status_code,
            }

        # Resend error envelope: {"name": "...", "message": "...", "statusCode": ...}
        error_message = (
            data.get("message")
            if isinstance(data, dict)
            else f"Resend HTTP {response.status_code}"
        )
        return {
            "success": False,
            "message": error_message or f"Resend HTTP {response.status_code}",
            "document": data if isinstance(data, dict) else {"raw": data},
            "status": response.status_code,
        }
            


    





#-------------------------------------------------MODEL/ENTITIES


    def list_entity(self,index,limit=50,lastkey=None):

        try:
            # Build the query parameters
            query_params = {
                'KeyConditionExpression': boto3.dynamodb.conditions.Key('index').eq(index),
                'Limit': int(limit)
            }
            
            # Add the ExclusiveStartKey to the query parameters if provided
            if lastkey:
                query_params['ExclusiveStartKey'] = {'index': index, 'ref': lastkey}

            # Query DynamoDB to get items with the same partition key
            response = self.entity_table.query(**query_params)
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey') # This will become the first in the next page 

            documents = {
                "items": items,
                "lastkey": endkey
            }

            return {
                "success":True, 
                "message": "Documents found", 
                "document": documents,
                "status" : response['ResponseMetadata']['HTTPStatusCode']
            }
        
        except ClientError as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }



    def get_entity(self,index,id):
   
        try:
            logger.debug('INDEX:'+index)
            logger.debug('ID:'+id)
            response = self.entity_table.get_item(Key={'index':index,'_id':id})
            item = response.get('Item')
            logger.debug('MODEL: get_entity:')
            logger.debug(response)
            logger.debug('MODEL: item:')
            logger.debug(item)
            

            if item:
                #return item
                return {
                    "success":True, 
                    "message": "Entity found", 
                    "document": item,
                    "status" : response['ResponseMetadata']['HTTPStatusCode']
                    }
            else:
                return {
                    "success":False, 
                    "message": "Entity not found",
                    "status" : 404
                    }
        except ClientError as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }
        
    
    def create_entity(self,data):

        data['modified'] = datetime.now().isoformat()
        
        try:
            response = self.entity_table.put_item(Item=data)
            logger.debug('MODEL: Created entity successfully:'+str(data))
            return {
                "success":True, 
                "message": "Entity created", 
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
        


    def update_entity(self,data):

        data['modified'] = datetime.now().isoformat()
        
        try:
            response = self.entity_table.put_item(Item=data)
            #logger.debug('MODEL: Updated entity successfully')
            return {
                "success":True, 
                "message": "Entity updated", 
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
        



    def delete_entity(self,**entity_document):

        keys = {
            'index': entity_document['index'],
            '_id': entity_document['_id']
        }

        try:
            response = self.entity_table.delete_item(Key=keys)
            logger.debug('MODEL: Deleted Entity:' + str(entity_document))
            return {
                "success":True,
                "message": "Entity deleted", 
                "document": entity_document,
                "status" : response['ResponseMetadata']['HTTPStatusCode'] 
                }
        
        except ClientError as e:
            return {
                "success":False,
                "message": e.response['Error']['Message'],
                "document": rel_document,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }




    def get_rel(self,index,rel):
   
        try:
            response = self.rel_table.get_item(Key={'index':index,'rel':rel})
            item = response.get('Item')

            if item:
                #return item
                return {
                    "success":True, 
                    "message": "Entity found", 
                    "document": item,
                    "status" : 200
                    }
            else:
                return {
                    "success":False, 
                    "message": "Entity not found",
                    "status" : 404
                    }
        except ClientError as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }
        

    
    def list_rel(self,index,limit=50,lastkey=None):

        try:
            # Build the query parameters
            query_params = {
                'KeyConditionExpression': boto3.dynamodb.conditions.Key('index').eq(index),
                'Limit': int(limit)
            }
            
            # Add the ExclusiveStartKey to the query parameters if provided
            if lastkey:
                query_params['ExclusiveStartKey'] = {'index': index, 'ref': lastkey}

            # Query DynamoDB to get items with the same partition key
            response = self.rel_table.query(**query_params)
            items = response.get('Items', [])
            endkey = response.get('LastEvaluatedKey') # This will become the first in the next page 

            documents = {
                "items": items,
                "lastkey": endkey
            }

            return {
                "success":True, 
                "message": "Documents found", 
                "document": documents,
                "status" : response['ResponseMetadata']['HTTPStatusCode']
            }
        
        except ClientError as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }
        

    def list_rel_prefix(self,partition_key_value,prefix):
        

        if not partition_key_value or not prefix:
            return {
                    "success":False, 
                    "message": 'Partition key and prefix are required',
                    "status" : 400
                    }

        try:
            # Query the table with the begins_with function on the sort key
            response = self.rel_table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('index').eq(partition_key_value) &
                                    boto3.dynamodb.conditions.Key('rel').begins_with(prefix)
            )

        
            return {
                "success":True, 
                "message": "Documents found", 
                "document": response['Items'],
                "status" : response['ResponseMetadata']['HTTPStatusCode']
            }

        except Exception as e:
            return {
                "success":False, 
                "message": e.response['Error']['Message'],
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }
   
        



    def create_rel(self, **rel_document):

        
        try:
            response = self.rel_table.put_item(Item=rel_document)
            logger.debug('MODEL: Created Relationship:' + str(rel_document))
            return {
                "success":True,
                "message": "Rel created", 
                "document": rel_document,
                "status" : response['ResponseMetadata']['HTTPStatusCode'] 
                }
        
        except ClientError as e:
            return {
                "success":False,
                "message": e.response['Error']['Message'],
                "document": rel_document,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }
        


    def delete_rel(self, **rel_document):

        keys = {
            'index': rel_document['index'],
            'rel': rel_document['rel']
        }

        try:
            response = self.rel_table.delete_item(Key=keys)
            logger.debug('MODEL: Deleted Relationship:' + str(rel_document))
            return {
                "success":True,
                "message": "Rel deleted", 
                "document": rel_document,
                "status" : response['ResponseMetadata']['HTTPStatusCode'] 
                }
        
        except ClientError as e:
            return {
                "success":False,
                "message": e.response['Error']['Message'],
                "document": rel_document,
                "status" : e.response['ResponseMetadata']['HTTPStatusCode']
                }







    
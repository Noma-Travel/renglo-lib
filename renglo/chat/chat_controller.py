# chat_controller.py
from flask import current_app
from renglo.chat.chat_model import ChatModel
from flask_cognito import current_cognito_jwt
from datetime import datetime
from ..common import *
import uuid
import json
import logging
import boto3
from decimal import Decimal

_logger_workspace = logging.getLogger("agent.workspace")

class ChatController:

    def __init__(self, config=None, tid=None, ip=None):
        """
        Initialize ChatController with configuration.

        Args:
            config (dict): Configuration dictionary containing WEBSOCKET_CONNECTIONS, etc.
            tid: Transaction ID (optional)
            ip: IP address (optional)
        """
        self.config = config or {}
        self.CHM = ChatModel(config=self.config, tid=tid, ip=ip)

    def error_chat(self, error, connection_id):

        try:
            websocket_url = self.config.get('WEBSOCKET_CONNECTIONS', '')
            self.apigw_client = boto3.client("apigatewaymanagementapi", endpoint_url=websocket_url)

        except Exception as e:
            _logger_workspace.error("websocket_init_failed | %s", e)
            self.apigw_client = None

        try:

            # WebSocket
            self.apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=error
            )

            return True

        except self.apigw_client.exceptions.GoneException:
            _logger_workspace.warning("websocket_connection_gone")
            return False
        except Exception as e:
            _logger_workspace.error("websocket_send_failed | %s", e)
            return False

    def get_current_user(self):

        current_app.logger.debug('Getting user')

        if "cognito:username" in current_cognito_jwt:
            # IdToken was used
            user_id = create_md5_hash(current_cognito_jwt["cognito:username"],9)
        else:
            # AccessToken was used
            user_id = create_md5_hash(current_cognito_jwt["username"],9)

        current_app.logger.debug('User Id: %s', user_id)

        return user_id

    # THREADS

    def list_threads(self,portfolio,org,entity_type,entity_id):

        #TO-DO : Check is this user has access to this tool before returning threads.

        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread:*/*"
        secondary = f"{entity_id}"

        # entity_id = ''  //This will return ALL the threads
        # entity_id = <entity_id_prefix>  //This will return everything that matches the prefix
        # entity_id = <entity_id_full> // This will return the exact match (one result)
        #>>>>

        limit = 10
        sort = 'desc'

        response = self.CHM.list_chat(index,secondary,limit,sort=sort)

        return response

    def query_threads(self,portfolio,org,entity_type,query):

        #TO-DO : Check is this user has access to this tool before returning threads.

        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread:*/*"
        query = f"{query}"

        limit = 99
        sort = 'desc'

        response = self.CHM.query_chat(index,query,limit,sort=sort)

        return response

    def create_thread(self,portfolio,org,entity_type,entity_id,public_user=''):

        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread:*/*"
        secondary = f"{entity_id}"

        if public_user:
            author_id = public_user
        else:
            author_id = self.get_current_user()

        data = {
            'author_id' : author_id,
            'time' : str(datetime.now().timestamp()),
            'is_active' : True,
            'entity_id' : entity_id,
            'entity_type' : entity_type,
            'entity_index' : secondary,
            'language' : 'EN',
            'index' : index,
            '_id':str(uuid.uuid4()),
        }

        response = self.CHM.create_chat(data)

        return response

    # TURNS
    # There is a document per turn in the database
    # Every turn document contains a list of messages that belong to that turn

    def list_turns(self, portfolio, org, entity_type, entity_id, thread_id, resolve=False):
        # resolve: UI /messages passes True for future tmp-artifact inlining; unused for now.
        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"

        query = f"{entity_id}/{thread_id}"

        limit = 50
        sort = 'asc'

        #print(f'List Turns params >> {index} , {query}')
        #response = self.CHM.list_chat(index,secondary,limit,sort=sort)
        response = self.CHM.query_chat(index, query, limit, sort=sort)

        #print(f'List Turns>> {response}')

        return response

    def get_turn(self,portfolio,org,entity_type, entity_id, thread_id, turn_id):

        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"
        query = f"{entity_id}/{thread_id}"

        # Because we don't have 'time' we need to get all the turns, iterate through
        # that list until we find the one that has the 'turn_id' and then return that.


        list_of_turns = self.list_turns(portfolio,org,entity_type,entity_id,thread_id)

        for t in list_of_turns['items']:
            if t['_id'] == turn_id:
                return {'success':True,'item':t}

        return {'success':False,'output':'Turn not found'}

    def create_turn(self,portfolio,org,entity_type, entity_id, thread_id, payload):
        try:
            if not all([entity_type, entity_id, thread_id, payload]):
                raise ValueError("Missing required parameters")

            index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"
            time = str(datetime.now().timestamp())
            secondary = f"{entity_id}/{thread_id}/{time}"

            current_app.logger.debug(f'create_turn > input > {index}/{secondary}')
            current_app.logger.debug(f'payload: {payload}')

            # Validate required payload fields
            required_fields = ['context']
            if not all(field in payload for field in required_fields):
                missing_fields = [field for field in required_fields if field not in payload]
                raise ValueError(f"Missing required payload fields: {missing_fields}")


            messages = []
            if 'messages' in payload and isinstance(payload['messages'], list):
                messages = payload['messages']

            if payload['context']['public_user']:
                author_id = payload['context']['public_user']
            else:
                author_id = self.get_current_user()

            data = {
                'author_id': author_id,
                'time': time,
                'is_active': True,
                'context': payload['context'],
                'messages': messages,
                'index': index,
                'entity_index': secondary,
                '_id': str(uuid.uuid4()) # This is the turn ID
            }

            current_app.logger.debug(f'Prepared data for chat creation: {data}')

            response = self.CHM.create_chat(data)
            return response

        except Exception as e:
            current_app.logger.error(f"Error in create_turn: {str(e)}")
            return {
                "success": False,
                "message": f"Error creating turn: {str(e)}",
                "status": 500
            }

    def _convert_floats_to_strings(self, obj):
        """
        Recursively converts float and Decimal values to strings in a dictionary or list structure.
        """
        if isinstance(obj, dict):
            return {k: self._convert_floats_to_strings(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_floats_to_strings(item) for item in obj]
        elif isinstance(obj, Decimal):
            # Convert Decimal to int if it's a whole number, otherwise float
            return int(obj) if obj % 1 == 0 else float(obj)
        elif isinstance(obj, float):
            return str(obj)
        return obj

    def update_turn(self,portfolio,org,entity_type, entity_id, thread_id, turn_id, update, call_id=False):
        # Sanitize update early to prevent serialization errors in logging
        update = self._convert_floats_to_strings(update)
        try:
            data = self.get_turn(portfolio,org,entity_type, entity_id, thread_id, turn_id)

            if not data['success']:
                return data

            # Get item from database - it will contain Decimals
            item = data['item']
            # Sanitize immediately to convert Decimals
            item = self._convert_floats_to_strings(item)
            #print(f'Document retrieved:{item}')

            if 'messages' not in item or not isinstance(item['messages'], list):
                item['messages'] = []

            if call_id:
                for i in item['messages']:
                    if 'tool_call_id' in i['_out'] and i['_out']['tool_call_id'] == call_id:
                        # Find the index of the item in the list
                        index = item['messages'].index(i)
                        # Parse JSON string to Python object and replace content
                        try:
                            parsed_content = json.loads(update['_out']['content'])

                            # Validate and normalize the parsed content
                            if isinstance(parsed_content, dict):
                                # If it's a single object, wrap it in a list
                                parsed_content = [parsed_content]
                            elif isinstance(parsed_content, list):
                                # If it's a list, validate that all items are dictionaries
                                if not all(isinstance(item, dict) for item in parsed_content):
                                    # If any item is not a dict, use original content
                                    parsed_content = update['_out']['content']
                            else:
                                # If it's neither dict nor list, use original content
                                parsed_content = update['_out']['content']

                            parsed_content = self._convert_floats_to_strings(parsed_content)
                            item['messages'][index]['_out']['content'] = parsed_content

                            if '_interface' in update:
                                item['messages'][index]['_interface'] = update['_interface']

                            if '_next' in update:
                                item['messages'][index]['_next'] = update['_next']


                        except json.JSONDecodeError as e:
                            _logger_workspace.error("json_parse_error | %s", e)
                            # If JSON parsing fails, keep the original string
                            parsed_content = self._convert_floats_to_strings(update['content'])
                            item['messages'][index]['_out']['content'] = parsed_content
            else:
                # Update is already sanitized at the beginning of the method
                item['messages'].append(update)

            #current_app.logger.debug(f'Prepared data for chat update: {item}')
            #print(f'Store modified item:{item}')
            response = self.CHM.update_chat(item)
            # print(response) verbose v2
            return response

        except Exception as e:
            current_app.logger.error(f"Error in update_turn: {str(e)}")
            return {
                "success": False,
                "message": f"Error updating message: {str(e)}",
                "status": 500
            }

    # WORKSPACE

    def list_workspaces(self,portfolio,org,entity_type,entity_id,thread_id):

        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*"
        query = f"{entity_id}/{thread_id}"

        limit = 50
        sort = 'asc'

        response = self.CHM.query_chat(index,query,limit,sort=sort)

        return response

    def get_workspace(self,portfolio,org,entity_type,entity_id,thread_id,workspace_id):

        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*"
        query = f"{entity_id}/{thread_id}"

        #print(f'get_workspace > INDEX:{index} , QUERY:{query}, TURN_ID:{workspace_id}')

        list_of_workspaces = self.list_workspaces(portfolio,org,entity_type,entity_id,thread_id)

        for w in list_of_workspaces['items']:
            if w['_id'] == workspace_id:
                return {'success':True,'item':w}

        return {'success':False,'output':'Workspace not found'}

    def create_workspace(self,portfolio,org,entity_type,entity_id,thread_id,payload):
        _logger_workspace.debug("create_workspace | running")
        try:

            if not all([entity_type, entity_id, thread_id]):
                raise ValueError("Missing required parameters")

            index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*"
            time = str(datetime.now().timestamp())
            secondary = f"{entity_id}/{thread_id}/{time}"

            current_app.logger.debug(f'create_workspace > input > {index}/{secondary}')
            current_app.logger.debug(f'payload: {payload}')

            # Validate required payload fields
            '''required_fields = ['context']
            if not all(field in payload for field in required_fields):
                missing_fields = [field for field in required_fields if field not in payload]
                raise ValueError(f"Missing required payload fields: {missing_fields}")'''

            context = {
                'entity_type':entity_type,
                'entity_id':entity_id,
                'thread_id':thread_id
            }

            state = {
                "beliefs": {},
                "goals": [],            # prioritized list of pending goals
                "intentions": [],       # current committed plans
                "history": [],          # log of completed intentions
                "in_progress": None     # the current active plan (intention)
            }

            _logger_workspace.debug("create_workspace | fields_ok")

            cache = {}
            if 'cache' in payload and isinstance(payload['cache'], dict):
                cache = payload['cache']

            config = {}
            if 'config' in payload and isinstance(payload['config'], dict):
                config = payload['config']

            type = 'json'
            if 'type' in payload and isinstance(payload['type'], str):
                type = payload['type']

            #Check if this is a Public user
            if payload.get('context', {}).get('public_user'):
                author_id = payload['context']['public_user']
            else:
                author_id = self.get_current_user()

            data = {
                'author_id':author_id,
                'time': time,
                'is_active': True,
                'context': context,
                'state': state,
                'type': type,
                'config' : config,
                'cache':cache,
                'index': index,
                'entity_index':secondary,
                '_id': str(uuid.uuid4())
            }

            current_app.logger.debug(f'Prepared data for chat creation: {data}')

            response = self.CHM.create_chat(data)
            return response

        except Exception as e:
            current_app.logger.error(f"Error in create_workspace: {str(e)}")
            return {
                "success": False,
                "message": f"Error creating workspace: {str(e)}",
                "status": 500
            }

    def update_workspace(self,portfolio,org,entity_type,entity_id,thread_id,workspace_id,payload):
        # Sanitize payload early to prevent serialization errors in logging
        payload = self._convert_floats_to_strings(payload)
        #print(f'CHC:update_workspace {entity_type}/{thread_id}/{workspace_id}')

        try:

            response_0 = self.get_workspace(portfolio,org,entity_type, entity_id, thread_id, workspace_id)

            #print('Updating the obtained workspace document...')

            if not response_0['success']:
                return response_0

            # Get item from database - it will contain Decimals
            item = response_0['item']
            # Sanitize immediately to convert Decimals
            item = self._convert_floats_to_strings(item)

            changed = False

            if 'state' in payload:
                item['state'] = payload['state']
                changed = True

            if 'cache' in payload:
                if 'cache' not in item:
                    item['cache'] = {}
                item['cache'] = payload['cache']
                changed = True

            if 'plan' in payload:
                item['plan'] = payload['plan']
                changed = True

            if 'state' in payload:
                item['state'] = payload['state']
                changed = True

            if 'state_machine' in payload:
                item['state_machine'] = payload['state_machine']
                changed = True

            if 'intent' in payload:
                item['intent'] = payload['intent']
                changed = True

            if changed:
                response = self.CHM.update_chat(item)
                return response
            else:
                _logger_workspace.debug("update_workspace | no_changes")

        except Exception as e:
            current_app.logger.error(f"Error in update_workspace: {str(e)}")
            return {
                "success": False,
                "message": f"Error updating workspace: {str(e)}",
                "status": 500
            }

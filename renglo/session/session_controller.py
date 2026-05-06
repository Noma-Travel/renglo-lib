# session_controller.py
from flask import current_app
from renglo.session.session_model import SessionModel
from flask_cognito import current_cognito_jwt
from datetime import datetime
from ..common import *
import uuid
import json
import boto3
import traceback
from decimal import Decimal


class SessionController:

    def __init__(self, config=None, tid=None, ip=None):
        """
        Initialize SessionController with configuration.
        
        Args:
            config (dict): Configuration dictionary
            tid: Transaction ID (optional)
            ip: IP address (optional)
        """
        self.config = config or {}
        self.SSM = SessionModel(config=self.config, tid=tid, ip=ip)
        
        
        
    def get_current_user(self):
        
        current_app.logger.debug(f'Getting user')

        if "cognito:username" in current_cognito_jwt:
            # IdToken was used
            user_id = create_md5_hash(current_cognito_jwt["cognito:username"],9)
        else:
            # AccessToken was used
            user_id = create_md5_hash(current_cognito_jwt["username"],9)
            
        current_app.logger.debug(f'User Id:{user_id}')

        return user_id
        
        
    # THREADS
        
    def list_threads(self,portfolio,org,entity_type,entity_id):
        
        #TO-DO : Check is this user has access to this tool before returning threads.
          
        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread:*/*"
        secondary = f"{entity_id}"

        
        # entity_id = ''  //This will return ALL the threads
        # entity_id = <entity_id_prefix>  //This will return everything that matches the prefix
        # entity_id = <entity_id_full> // This will return the exact match (one result)
        #>>>>
        
        limit = 10
        sort = 'desc'
        
        response = self.SSM.list_session(index,secondary,limit,sort=sort)
        
        return response
     
    def query_threads(self,portfolio,org,entity_type,query):
        
        #TO-DO : Check is this user has access to this tool before returning threads.  
        
        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread:*/*"
        query = f"{query}"

        
        
        limit = 99
        sort = 'desc'
        
        response = self.SSM.query_session(index,query,limit,sort=sort)
        
        return response
         
    
    def create_thread(self,portfolio,org,entity_type,entity_id,public_user=''):
        

        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread:*/*"
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
        
        response = self.SSM.create_session(data)
        
        return response
    

    
    # TURNS
    # There is a document per turn in the database; entries live under ``events``.

    def _turn_entries(self, item: dict) -> list:
        """Return the mutable list of turn entries under ``events`` (empty list if missing or wrong type)."""
        ev = item.get("events")
        if isinstance(ev, list):
            return ev
        item["events"] = []
        return item["events"]

    def list_turns(self, portfolio, org, entity_type, entity_id, thread_id, resolve=False):
        # resolve: same contract as chat list_turns; unused for now.
        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"

        query = f"{entity_id}/{thread_id}"

        limit = 50
        sort = 'asc'

        #print(f'List Turns params >> {index} , {query}')
        #response = self.SSM.list_session(index,secondary,limit,sort=sort)
        response = self.SSM.query_session(index, query, limit, sort=sort)

        #print(f'List Turns>> {response}')

        return response
    
    
    def get_turn(self,portfolio,org,entity_type, entity_id, thread_id, turn_id):
        
        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"
        query = f"{entity_id}/{thread_id}"

        
        # Because we don't have 'time' we need to get all the turns, iterate through
        # that list until we find the one that has the 'turn_id' and then return that.
        
        print(f'get_turn > INDEX:{index} , QUERY:{query}, TURN_ID:{turn_id}') 
        
        list_of_turns = self.list_turns(portfolio,org,entity_type,entity_id,thread_id)
        
        for t in (list_of_turns or {}).get("items") or []:
            if t['_id'] == turn_id:
                return {'success':True,'item':t}
          
        return {'success':False,'output':'Turn not found'}
    
    
    def create_turn(self,portfolio,org,entity_type, entity_id, thread_id, payload):
        print('SSC:create_turn')
        try:
            if not all([entity_type, entity_id, thread_id, payload]):
                raise ValueError("Missing required parameters")

            index = f"irn:session:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"
            time = str(datetime.now().timestamp())
            secondary = f"{entity_id}/{thread_id}/{time}"
            
            
            current_app.logger.debug(f'create_turn > input > {index}/{secondary}')
            current_app.logger.debug(f'payload: {payload}')
            
            # Validate required payload fields
            required_fields = ['context']
            if not all(field in payload for field in required_fields):
                missing_fields = [field for field in required_fields if field not in payload]
                raise ValueError(f"Missing required payload fields: {missing_fields}")
            
            print('All fields required: OK')
            
            events: list = []
            if "events" in payload and isinstance(payload["events"], list):
                events = payload["events"]
                
            if payload['context']['public_user']:
                author_id = payload['context']['public_user']
            else:
                author_id = self.get_current_user()
            
            data = {
                'author_id': author_id,
                'time': time,
                'is_active': True,
                'context': payload['context'],
                'events': events,
                'index': index,
                'entity_index': secondary,
                '_id': str(uuid.uuid4()) # This is the turn ID 
            }
            
            current_app.logger.debug(f'Prepared data for session creation: {data}')
            
            response = self.SSM.create_session(data)
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
        print(f'SSC:update_turn {entity_type}/{thread_id}/{turn_id}::{call_id}')
        try:
            data = self.get_turn(portfolio,org,entity_type, entity_id, thread_id, turn_id)
            
            if not data['success']:
                return data
            
            # Get item from database - it will contain Decimals
            item = data['item']
            # Sanitize immediately to convert Decimals
            item = self._convert_floats_to_strings(item)
            #print(f'Document retrieved:{item}')
            
            entries = self._turn_entries(item)
            
            if call_id: 
                print('Call id found:')  
                print(entries)
                for i in entries:
                    if 'tool_call_id' in i['_out'] and i['_out']['tool_call_id'] == call_id:
                        print(f'Found the message with matching id:{i}')
                        print(f'Replacing with new doc:{update}') 
                        # Find the index of the item in the list
                        index = entries.index(i)
                        # Parse JSON string to Python object and replace content
                        try:
                            parsed_content = json.loads(update['_out']['content'])
                            
                            # Validate and normalize the parsed content
                            if isinstance(parsed_content, dict):
                                # If it's a single object, wrap it in a list
                                parsed_content = [parsed_content]
                            elif isinstance(parsed_content, list):
                                # If it's a list, validate that all items are dictionaries
                                if not all(isinstance(elem, dict) for elem in parsed_content):
                                    # If any item is not a dict, use original content
                                    parsed_content = update['_out']['content']
                            else:
                                # If it's neither dict nor list, use original content
                                parsed_content = update['_out']['content']
                                
                            parsed_content = self._convert_floats_to_strings(parsed_content)
                            entries[index]['_out']['content'] = parsed_content
                            
                            if '_interface' in update:
                                entries[index]['_interface'] = update['_interface']
                                
                            if '_next' in update:
                                entries[index]['_next'] = update['_next']
                                
                                
                            print(entries[index])
                            
                            
                        except json.JSONDecodeError as e:
                            print(f"Error parsing JSON content: {e}")
                            out = update.get("_out") or {}
                            fallback = out.get("content", "")
                            parsed_content = self._convert_floats_to_strings(fallback)
                            entries[index]['_out']['content'] = parsed_content
            else:
                # Update is already sanitized at the beginning of the method
                entries.append(update)
            
            #current_app.logger.debug(f'Prepared data for session update: {item}')
            #print(f'Store modified item:{item}')
            response = self.SSM.update_session(item)
            print(response) 
            return response
        
        except Exception as e:
            current_app.logger.error(f"Error in update_turn: {str(e)}")
            current_app.logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Error updating message: {str(e)}",
                "status": 500
            }
        
        
        
    # WORKSPACE
    
    def list_workspaces(self,portfolio,org,entity_type,entity_id,thread_id):
              
        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*"
        query = f"{entity_id}/{thread_id}"
        
        
        
        limit = 50
        sort = 'asc'
        
        response = self.SSM.query_session(index,query,limit,sort=sort)
        
        return response
    
    
    def get_workspace(self,portfolio,org,entity_type,entity_id,thread_id,workspace_id):
        
        index = f"irn:session:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*" 
        query = f"{entity_id}/{thread_id}"
        
        #print(f'get_workspace > INDEX:{index} , QUERY:{query}, TURN_ID:{workspace_id}') 
        
        list_of_workspaces = self.list_workspaces(portfolio,org,entity_type,entity_id,thread_id)
        
        for w in list_of_workspaces['items']:
            if w['_id'] == workspace_id:
                return {'success':True,'item':w}
          
        return {'success':False,'output':'Workspace not found'}
    
    
    def create_workspace(self,portfolio,org,entity_type,entity_id,thread_id,payload):
        print('SSC:create_workspace')
        try:
            
            if not all([entity_type, entity_id, thread_id]):
                raise ValueError("Missing required parameters")

            index = f"irn:session:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*"
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
            

            print('All fields required: OK')
            
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
            
            current_app.logger.debug(f'Prepared data for session creation: {data}')
            
            response = self.SSM.create_session(data)
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
        #print(f'SSC:update_workspace {entity_type}/{thread_id}/{workspace_id}')
        
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
                #print('Something has changed. Updating the workspace')
                #current_app.logger.debug(f'Prepared data for workspace update: {item}')
                print(item)
                response = self.SSM.update_session(item)
                print('Workspace has been updated.')
                #print(response)
                return response
            else:
                print('No changes detected in workspace.')
        
        except Exception as e:
            current_app.logger.error(f"Error in update_workspace: {str(e)}")
            return {
                "success": False,
                "message": f"Error updating workspace: {str(e)}",
                "status": 500
            }
            
            
    
    
                
            
    
    

            
        
        
        
        
        
        
        
    
        
        
    
    
    
   
    
    
    
    
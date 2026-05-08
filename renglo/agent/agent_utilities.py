from renglo.data.data_controller import DataController
from renglo.docs.docs_controller import DocsController
from renglo.chat.chat_controller import ChatController
from renglo.schd.schd_controller import SchdController
from renglo.agent.websocket_client import WebSocketClient

from langfuse.openai import OpenAI

import random
import json
from datetime import datetime
from typing import List, Dict, Any, Callable
import re
from decimal import Decimal
import time
import uuid
from renglo.debug_json import djson
import logging

_logger_workspace = logging.getLogger("agent.workspace")
_logger_llm = logging.getLogger("agent.llm")

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)

class AgentUtilities:
    def __init__(self,
                 config,
                 portfolio,
                 org,
                 entity_type,
                 entity_id,
                 thread,
                 connection_id = None
                 ):
        """
        Initialize AgentUtilities with configuration and required parameters.

        Args:
            config (dict): Configuration dictionary containing API keys, URLs, etc.

        Args:
            portfolio (str): Portfolio identifier
            org (str): Organization identifier
            entity_type (str): Type of entity
            entity_id (str): Entity identifier
            thread (str): Thread identifier
        """
        self.config = config or {}
        self.portfolio = portfolio
        self.org = org
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.thread = thread
        self.chat_id = None
        self.connection_id = connection_id

        # OpenAI Client
        try:
            openai_key = self.config.get('OPENAI_API_KEY', '')
            self.AI_1 = OpenAI(api_key=openai_key)
            self.AI_2 = OpenAI(api_key=openai_key)
        except Exception as e:
            _logger_llm.error("openai_init_failed | %s", e)
            self.AI_1 = None
            self.AI_2 = None

        self.AI_1_MODEL = "gpt-3.5-turbo"  # Baseline model. Good for multi-step chats
        self.AI_2_MODEL = "gpt-4o-mini"    # This model is not very smart

        # Initialize controllers
        self.DAC = DataController(config=self.config)
        self.DCC = DocsController(config=self.config)
        self.CHC = ChatController(config=self.config)
        self.SHC = SchdController(config=self.config)

        # Initialize WebSocket client
        websocket_url = self.config.get('WEBSOCKET_CONNECTIONS', '')
        self.ws_client = WebSocketClient(websocket_url)

    def get_message_history(self,filter={}):
        """
        Get the message history for the current thread.
        filter:{'param':<name>,'begins_with':<value>}
            param: The name of the parameter you are applying the filter: ['_interface','_next','_type']
            value: The begins at string for the value of the parameter to be filtered

        Returns:
            dict: Success status and message list
        """
        action = 'get_message_history'

        try:
            _logger_workspace.debug("get_message_history | type=%s | entity_id=%s | thread=%s | filter=%s", self.entity_type, self.entity_id, self.thread, filter)

            apply_filter = False
            filter_param = None
            filter_value = None

            if filter and 'param' in filter and 'begins_with' in filter:
                filter_param = filter['param']
                filter_value = filter['begins_with']
                apply_filter = True

            # Thread was not included, create a new one?
            if not self.thread:
                return {'success': False, 'action': action, 'input': filter, 'output': 'Error: No thread provided'}

            response = self.CHC.list_turns(
                self.portfolio,
                self.org,
                self.entity_type,
                self.entity_id,
                self.thread
            )

            if 'success' not in response:
                _logger_workspace.error("message_list_failed | %s", response)
                return {'success': False, 'action': action, 'input': filter, 'output': response}

            # Prepare messages to look like an OpenAI message array
            # Also remove messages that don't belong to an approved type
            message_list = []
            for turn in response['items']:
                for m in turn['messages']:

                    if apply_filter:
                        #print(f'Applying filter in message:{m}')
                        # Check if filter_param exists in message
                        if filter_param not in m or m[filter_param] is None:
                            #print('Param did not exist, filter out')
                            continue
                        # Convert to string for comparison and check if value begins with filter_value
                        param_value = str(m[filter_param])
                        if not param_value.startswith(filter_value):
                            #print(f'{param_value} does not begin with:{filter_value}, filter out')
                            continue

                        _logger_workspace.debug("message_filter_include | param=%s | value=%s", filter_param, param_value)

                    out_message = m['_out']
                    if m['_type'] in ['user', 'consent', 'system', 'text', 'tool_rq', 'tool_rs']:  # OK to show to LLM
                        message_list.append(out_message)

            djson("message_history.json", message_list)
            return {'success': True, 'action': action, 'input': filter, 'output': message_list}

        except Exception as e:
            _logger_workspace.error("get_message_history_failed | %s", e)
            return {'success': False, 'action': action, 'input': filter, 'output': f'Error: {str(e)}'}

    def update_chat_message_document(self, update, call_id=False):
        """
        Update a chat message document.

        Args:
            update (dict): The update to apply
            call_id (bool): Whether to use call_id

        Returns:
            dict: Success status and response
        """
        action = 'update_chat_message_document'
        _logger_workspace.debug("update_chat_message_document | running")

        try:
            response = self.CHC.update_turn(
                self.portfolio,
                self.org,
                self.entity_type,
                self.entity_id,
                self.thread,
                self.chat_id,
                update,
                call_id=call_id
            )

            if 'success' not in response:
                _logger_workspace.error("update_chat_message_failed | %s", response)
                return {'success': False, 'action': action, 'input': update, 'output': response}

            return {'success': True, 'action': action, 'input': update, 'output': response}

        except Exception as e:
            _logger_workspace.error("update_chat_message_failed | %s", e)
            return {'success': False, 'action': action, 'output': f'Error: {str(e)}'}

    def update_workspace_document(self, update, workspace_id):
        """
        Update a workspace document.

        Args:
            update (dict): The update to apply
            workspace_id (str): The workspace ID

        Returns:
            dict: Success status and response
        """
        action = 'update_workspace_document'
        #print(f'Running: {action}')

        response = self.CHC.update_workspace(
            self.portfolio,
            self.org,
            self.entity_type,
            self.entity_id,
            self.thread,
            workspace_id,
            update
        )

        if 'success' not in response:
            return {'success': False, 'action': action, 'input': update, 'output': response}

        return {'success': True, 'action': action, 'input': update, 'output': response}

    def save_chat(self, output, interface=None, connection_id=None, next=None, msg_type=None):

        function = 'save_chat'
        """
        Save chat message to storage and context.

        Args:
            output (dict): The output to save
            interface (string): name of the interface

        Input:
            {
                'role':'',
                'tool_calls':'',
                'content':''
            }
        """
        try:
            if msg_type == 'consent':
                _logger_workspace.debug("save_chat | type=consent")
                # This is a consent request from the agent to the user
                message_type = 'consent'
                if not interface:
                    interface = 'binary_consent'
                doc = {'_out': self.sanitize(output), '_type': 'consent','_interface':interface,'_next': next}
                self.update_chat_message_document(doc)
                self.print_chat(doc,message_type, as_is=True)

            elif msg_type == 'widget':
                _logger_workspace.debug("save_chat | type=widget")
                # This is a consent request from the agent to the user
                message_type = 'widget'
                if not interface:
                    interface = ''
                doc = {'_out': self.sanitize(output), '_type': 'widget','_interface':interface}
                self.update_chat_message_document(doc)
                self.print_chat(doc,message_type, as_is=True)

            elif msg_type == 'transient' and output.get('content') and output.get('role') == 'assistant':
                _logger_workspace.debug("save_chat | type=transient")
                message_type = 'transient'
                doc = {'_out': self.sanitize(output), '_type': message_type, '_next': next}
                self.update_chat_message_document(doc)
                self.print_chat(doc, message_type, as_is=True)

            elif msg_type == 'internal' and output.get('role') == 'assistant':
                _logger_workspace.debug("save_chat | type=internal")
                message_type = 'internal'
                doc = {'_out': self.sanitize(output), '_type': message_type, '_next': next}
                self.update_chat_message_document(doc)

            elif msg_type == 'json' and output.get('role') == 'assistant':
                message_type = 'json'
                out_val = output.get('content') if 'content' in output else output
                doc = {'_out': self.sanitize(out_val), '_type': message_type, '_interface': interface or 'document', '_next': next}
                self.update_chat_message_document(doc)
                self.print_chat(doc, message_type, as_is=True)

            elif msg_type == 'option' and output.get('role') == 'assistant':
                message_type = 'option'
                out_val = output.get('content') if 'content' in output else output
                doc = {'_out': self.sanitize(out_val), '_type': message_type, '_interface': interface or 'option', '_next': next}
                self.update_chat_message_document(doc)
                self.print_chat(doc, message_type, as_is=True)


            elif output.get('tool_calls') and output.get('role') == 'assistant':
                _logger_workspace.debug("save_chat | type=tool_rq")
                # This is a tool call
                message_type = 'tool_rq'
                tool_rq_out = dict(output)
                tc_raw = tool_rq_out.get('content')
                if tc_raw is None or (
                    isinstance(tc_raw, str) and tc_raw.strip() in ('None', 'null')
                ):
                    tool_rq_out['content'] = ''
                doc = {'_out': self.sanitize(tool_rq_out), '_type': 'tool_rq','_next': next}
                # Memorize to permanent storage
                self.update_chat_message_document(doc)

                # Creating empty placeholders corresponding to each one of the un-executed tool calls.
                # This was a work-around as OpenAI doesn't like to see a tools_calls without its corresponding response.
                # It happens because sometimes, the chat messages are passed to the LLM before the tool is executed
                # (e.g: Asking the user for approval to use a tool, the agent needs to understand the response using an LLM)
                for tool_call in output['tool_calls']:
                    rs_template = {
                        "role": "tool",
                        "tool_call_id": tool_call['id'],
                        "content": []
                    }
                    _logger_workspace.debug("save_chat | type=tool_rs_placeholder | id=%s", tool_call['id'])
                    doc_rs_placeholder = {'_out': rs_template, '_type': 'tool_rs','_next': next}
                    self.update_chat_message_document(doc_rs_placeholder)

            elif output.get('content') and output.get('role') == 'assistant':
                _logger_workspace.debug("save_chat | type=text")
                # This is a human readable message from the agent to the user
                message_type = 'text'
                doc = {'_out': self.sanitize(output), '_type': message_type, '_next': next}
                # Memorize to permanent storage
                response_1 = self.update_chat_message_document(doc)
                #print(f'Chat update response:',response_1)
                # Print to live chat
                self.print_chat(doc, message_type, as_is=True)
                # Print to API
                self.print_api(output['content'], message_type)

            elif 'tool_call_id' in output and 'role' in output and output['role'] == 'tool':
                # This is the tool response
                message_type = 'tool_rs'
                _logger_workspace.debug("save_chat | type=tool_rs | interface=%s", interface)
                doc = {'_out': self.sanitize(output), '_type': message_type, '_interface': interface, '_next': next}
                # Memorize to permanent storage (DB path keeps content as string)
                self.update_chat_message_document(doc, output['tool_call_id'])

                if interface:
                    # For WebSocket, mirror ChatController.update_turn's normalization:
                    # - If parsed content is an object (dict), wrap it in a single-element list.
                    # - If it's a list of dicts, keep as-is.
                    # - Otherwise (string/number/etc.), leave the original string.
                    doc_for_websocket = doc.copy()
                    if '_out' in doc_for_websocket and 'content' in doc_for_websocket['_out']:
                        content = doc_for_websocket['_out']['content']
                        if isinstance(content, str):
                            try:
                                parsed_content = json.loads(content)
                                if isinstance(parsed_content, dict):
                                    parsed_content = [parsed_content]
                                elif isinstance(parsed_content, list):
                                    if not all(isinstance(item, dict) for item in parsed_content):
                                        parsed_content = content
                                else:
                                    parsed_content = content
                                if parsed_content is not content:
                                    doc_for_websocket['_out'] = doc_for_websocket['_out'].copy()
                                    doc_for_websocket['_out']['content'] = parsed_content
                            except (json.JSONDecodeError, TypeError):
                                # If parsing fails, keep original string
                                pass
                    self.print_chat(doc_for_websocket, message_type, as_is=True)

        except Exception as e:
            _logger_workspace.error("save_chat_failed | fn=%s | %s", function, e)

    def print_api(self, message, type='text', public_user=None):
        """
        Print message to API.

        Args:
            message (str): The message to print
            type (str): The message type
            public_user (str): The public user identifier

        Returns:
            dict: Success status and response
        """
        action = 'print_api'

        callback_msg_handler = self.config.get('CALLBACK_MSG_HANDLER', False)

        try:
            if callback_msg_handler:
                if public_user:
                    target = public_user
                else:
                    return {'success': False, 'action': action, 'input': message, 'output': 'This is an internal call, no API output is needed'}

                params = {'message': message, 'type': type, 'target': target}

                parts = callback_msg_handler.split('/')
                if len(parts) != 2:
                    error_msg = f"{callback_msg_handler} is not a valid tool."
                    _logger_workspace.error("print_api_invalid_handler | %s", error_msg)
                    self.print_chat(error_msg, 'error')
                    raise ValueError(error_msg)

                _logger_workspace.debug("print_api_calling | %s", callback_msg_handler)
                response = self.SHC.handler_call(self.portfolio, self.org, parts[0], parts[1], params)

                return response
            else:
                return {'success': False, 'action': action, 'input': message, 'output': ''}

        except ValueError as ve:
            _logger_workspace.error("print_api_failed | %s", ve)
            return {'success': False, 'action': action, 'input': message, 'output': str(ve)}
        except Exception as e:
            _logger_workspace.error("print_api_failed | %s", e)
            return {'success': False, 'action': action, 'input': message, 'output': str(e)}

    def print_chat(self, output, type='text', as_is=False, connection_id=None, next= None):
        """
        Print message to chat via WebSocket.

        Args:
            output: The output to print
            type (str): The message type
            as_is (bool): Whether to use output as-is
            connection_id (str): The WebSocket connection ID

        Returns:
            bool: Success status
        """
        _logger_workspace.debug("print_chat | type=%s", type)

        if not connection_id:
            #Try the context
            connection_id = self.connection_id

        if as_is:
            doc = output
        elif isinstance(output, dict) and 'role' in output and 'content' in output and output['role'] and output['content']:
            # Content responses from LLM
            doc = {'_out': {'role': output['role'], 'content': self.sanitize(output['content'])}, '_type': type, '_next:':next}
        elif isinstance(output, str):
            # Any text response
            doc = {'_out': {'role': 'assistant', 'content': str(output)}, '_type': type, '_next:':next}
        else:
            # Everything else
            doc = {'_out': {'role': 'assistant', 'content': self.sanitize(output)}, '_type': type, '_next:':next}

        if not connection_id:
            #print(f'WebSocket not configured or this is a RESTful post to the chat.')
            return False

        if not self.ws_client.is_configured():
            return False

        #print(f'Sending Websocket Message to client. ConnectionId:{connection_id}')
        success = self.ws_client.send_message(connection_id, doc)

        if success:
            _logger_workspace.debug("print_chat | ws_sent=True")

        return success

    # Helper function to safely get a step in the state machine by step_id
    def get_or_create_step(self, workspace, plan_id, plan_step):
        """
        Safely get a step in the state machine by matching step_id.
        The plan_step parameter is the step_id (not a list index).
        We search through the steps list to find the step with matching step_id.

        Args:
            workspace: The workspace dictionary
            plan_id: The plan ID
            plan_step: The step_id to find (as string or int)

        Returns:
            dict: The step dictionary with matching step_id

        Raises:
            IndexError: If the step with the given step_id is not found
        """
        if 'state_machine' not in workspace:
            workspace['state_machine'] = {}

        if plan_id not in workspace['state_machine']:
            workspace['state_machine'][plan_id] = {'steps': []}

        if 'steps' not in workspace['state_machine'][plan_id]:
            workspace['state_machine'][plan_id]['steps'] = []

        steps = workspace['state_machine'][plan_id]['steps']
        target_step_id = str(plan_step)  # Normalize to string for comparison

        # Search for the step by step_id (not by index)
        for step in steps:
            # Handle both string and int step_id values
            step_id = str(step.get('step_id', ''))
            if step_id == target_step_id:
                return step

        # If step not found, raise an error - we should not create steps that don't exist in the plan
        raise IndexError(f"Step with step_id '{plan_step}' not found in state machine for plan_id '{plan_id}'. The state machine has {len(steps)} step(s).")

    def mutate_workspace(self, changes, public_user=None, workspace_id=None):
        """
        Mutate workspace with changes.

        Args:
            changes (dict): The changes to apply
            public_user (str): The public user identifier
            workspace_id (str): The workspace ID

        Returns:
            bool: Success status
        """
        try:

            if not self.thread:
                return False

            if public_user:
                payload = {'context': {'public_user': public_user}}
            else:
                payload = {}

            # Sanitize changes early to prevent serialization errors in logging
            changes = self.sanitize(changes)
            _logger_workspace.debug("workspace_mutation | keys=%s", list(changes.keys()) if isinstance(changes, dict) else '?')

            # 1. Get the workspace in this thread
            #print(f'Looking for workspaces @:{self.portfolio}:{self.org}:{self.entity_type}:{self.entity_id}:{self.thread} ')
            workspaces_list = self.CHC.list_workspaces(
                self.portfolio,
                self.org,
                self.entity_type,
                self.entity_id,
                self.thread
            )
            #print('WORKSPACES_LIST >>', workspaces_list) #verbose

            if not workspaces_list['success']:
                return False

            if len(workspaces_list['items']) == 0:
                # Create a workspace as none exist
                response = self.CHC.create_workspace(
                    self.portfolio,
                    self.org,
                    self.entity_type,
                    self.entity_id,
                    self.thread, payload
                )
                if not response['success']:
                    return False
                # Regenerate workspaces_list
                workspaces_list = self.CHC.list_workspaces(
                    self.portfolio,
                    self.org,
                    self.entity_type,
                    self.entity_id,
                    self.thread
                )
                #print('UPDATED WORKSPACES_LIST >>>>', workspaces_list) #Verbose

            if not workspace_id:
                workspace = workspaces_list['items'][-1]
            else:
                for w in workspaces_list['items']:
                    if w['_id'] == workspace_id:
                        workspace = w

            # CRITICAL: Sanitize workspace immediately after retrieval from database
            # This converts all Decimals before any merging or manipulation
            workspace = self.sanitize(workspace)
            #print('Selected workspace >>>>', workspace) #Verbose
            if 'state' not in workspace:
                workspace['state'] = {
                    "beliefs": {},
                    "desire": '',
                    "intent": [],
                    "history": [],
                    "in_progress": None
                }

            # 2. Store the output in the workspace
            for key, output in changes.items():
                if key == 'belief':
                    # output = {"date":"345"}
                    if isinstance(output, dict):
                        # Sanitize output before merging, then sanitize the merged result
                        sanitized_output = self.sanitize(output)
                        merged_beliefs = {**workspace['state']['beliefs'], **sanitized_output}
                        workspace['state']['beliefs'] = self.sanitize(merged_beliefs)

                if key == 'desire':
                    if isinstance(output, str):
                        workspace['state']['desire'] = output

                if key == 'intent':
                    _logger_workspace.debug("mutate_intent | inserting=%s", output)
                    if isinstance(output, dict):
                        # Sanitize nested intent data to ensure no Decimals slip through
                        workspace['state']['intent'] = self.sanitize(output)

                if key == 'belief_history':
                    if isinstance(output, dict):
                        # Now update the belief history
                        for k, v in output.items():
                            history_event = {
                                'type': 'belief',
                                'key': k,
                                'val': self.sanitize(v),
                                'time': datetime.now().isoformat()
                            }
                            workspace['state']['history'].append(history_event)

                if key == 'cache':
                    _logger_workspace.debug("workspace_cache_update")
                    if 'cache' not in workspace:
                        workspace['cache'] = {}
                    if isinstance(output, dict):
                        for k, v in output.items():
                            # Sanitize nested values to ensure no Decimals slip through
                            workspace['cache'][k] = self.sanitize(v)
                    elif isinstance(output, list):
                        # For lists, sanitize each element and store as 'results'
                        workspace['cache']['results'] = self.sanitize(output)

                if key == 'is_active':
                    if isinstance(output, bool):
                        workspace['data'] = output  # Output overrides existing data

                if key == 'action':
                    if isinstance(output, str):
                        workspace['state']['action'] = output  # Output overrides existing data

                if key == 'follow_up':
                    if isinstance(output, dict):
                        # Sanitize nested follow_up data to ensure no Decimals slip through
                        workspace['state']['follow_up'] = self.sanitize(output)

                if key == 'slots':
                    if isinstance(output, dict):
                        # Sanitize nested slots data to ensure no Decimals slip through
                        workspace['state']['slots'] = self.sanitize(output)

                if key == 'plan':
                    if isinstance(output, dict):
                        plan_id = output['id']
                        if 'plan' not in workspace:
                            workspace['plan'] = {}
                        # Sanitize nested plan data to ensure no Decimals slip through
                        workspace['plan'][plan_id] = self.sanitize(output)

                if key == 'new_state_machine':
                    _logger_workspace.debug("state_machine_init")
                    if isinstance(output, dict):
                        plan_id = output['plan_id']
                        if 'state_machine' not in workspace:
                            workspace['state_machine'] = {}
                        # Sanitize nested plan data to ensure no Decimals slip through
                        if plan_id not in workspace['state_machine']:
                            # It won't override entire state machine if it already exists.
                            workspace['state_machine'][plan_id] = self.sanitize(output)

                if key == 'step_state':

                    #print(f'mutate step_state input:{output}')
                    if isinstance(output, dict):

                        if 'plan_id' in output and 'plan_step' in output:
                            plan_id = output['plan_id']
                            plan_step = output['plan_step']

                            # Use helper function to safely get or create the step
                            step = self.get_or_create_step(workspace, plan_id, plan_step)

                            if 'status' in output:
                                step['status'] = output['status']
                            if 'error' in output:
                                step['error'] = output['error']
                            if 'started_at' in output:
                                step['started_at'] = output['started_at']
                            if 'finished_at' in output:
                                step['finished_at'] = output['finished_at']

                    #print(f'State Machine after mutate step_state:{workspace["state_machine"]}')

                if key == 'plan_state':
                    if isinstance(output, dict):

                        _logger_workspace.debug("mutate_plan_state | %s", output)

                        if 'plan_id' in output :
                            plan_id = output['plan_id']

                            if 'status' in output:
                                workspace['state_machine'][plan_id] = output['status']
                            if 'updated_at' in output:
                                workspace['state_machine'][plan_id] = output['updated_at']

                if key == 'completion_router':
                    if isinstance(output, dict):
                        workspace['completion_router'] = self.sanitize(output)

                if key == 'action_log':
                    if isinstance(output, dict):
                        '''
                        {
                            "plan_id":plan_id,
                            "plan_step":plan_step,
                            "tool":selected_tool,
                            "status":tool_step,
                            "nonce":nonce,
                            "message":message,
                            "type":type
                        }
                        '''
                        # Storing action_log:{'plan_id': 'd6e47334', 'plan_step': '0', 'tool': 'search_flights', 'status': 3, 'details': {'commands': [{'id': 'call_tMtY0uDa3WAnl9kyz9MqXnhA', 'function': {'arguments': '{"from_airport_code":"DFW","to_airport_code":"JFK","outbound_date":"2026-01-25","return_date":"2026-02-01"}', 'name': 'search_flights'}, 'type': 'function'}], 'interface': 'binary_consent', 'nonce': 116360, 'message': {'role': 'assistant', 'content': 'I would like to call search_flights tool with the following parameters:from_airport_code: DFW, to_airport_code: JFK, outbound_date: 2026-01-25, return_date: 2026-02-01. Please confirm it is ok'}}}

                        _logger_workspace.debug("storing_action_log")
                        plan_id = output['plan_id']
                        plan_step = output['plan_step']
                        log = {}
                        if 'tool' in output:
                            log['tool'] = output['tool']
                        if 'status' in output:
                            log['status'] = output['status']
                        if 'nonce' in output:
                            log['nonce'] = output['nonce']
                        if 'message' in output:
                            log['message'] = output['message']
                        if 'type' in output:
                            log['type'] = output['type']
                        if 'actionable' in  output:
                            log['actionable'] = output['actionable']

                        # Use helper function to safely get or create the step
                        step = self.get_or_create_step(workspace, plan_id, plan_step)

                        if 'action_log' not in step:
                            step['action_log'] = []

                        step['action_log'].append(log)


             # 3. Update document in DB

            # Sanitize the entire workspace object to convert Decimals before updating
            sanitized_workspace = self.sanitize(workspace)
            self.update_workspace_document(
                sanitized_workspace,
                workspace['_id']
            )
            djson("workspace.json", workspace)
            return True

        except Exception as e:
            _logger_workspace.error("workspace_mutation_failed | %s", e)
            return False

    def llm(self, prompt):
        """
        Call the LLM with the given prompt.

        Args:
            prompt (dict): The prompt to send to the LLM

        Returns:
            The LLM response or False if error
        """

        try:
            # Create base parameters
            params = {
                'model': '',
                'messages': '',
                'temperature': 0.0
            }

            # Add optional parameters if they exist
            if 'model' in prompt:
                params['model'] = prompt['model']
            if 'messages' in prompt:
                params['messages'] = prompt['messages']
            if 'temperature' in prompt:
                params['temperature'] = prompt['temperature']
            if 'tools' in prompt:
                params['tools'] = prompt['tools']
            if 'tool_choice' in prompt:
                params['tool_choice'] = prompt['tool_choice']
            if 'parallel_tool_calls' in prompt:
                params['parallel_tool_calls'] = prompt['parallel_tool_calls']
            if 'response_format' in prompt:
                params['response_format'] = prompt['response_format']

            model = params['model']
            messages = params['messages']
            tools = params.get('tools')
            _logger_llm.info("llm call model=%s messages=%d tools=%d", model, len(messages) if isinstance(messages, list) else 0, len(tools or []))
            djson("llm_last_prompt.json", {"model": model, "messages": messages, "tools": tools})

            # AI_1 is gpt-3.5-turbo which doesn't support structured outputs. AI_2 uses gpt-4o-mini which does.
            # response = self.AI_1.chat.completions.create(**params)
            response = self.AI_2.chat.completions.create(**params)

            resp = response.choices[0].message
            _logger_llm.info("llm result returned successfully and has_tool_calls=%s", bool(resp.tool_calls))
            djson("llm_last_response.json", {"role": resp.role, "content": resp.content, "tool_calls": [{"id": tc.id, "function": {"name": tc.function.name}} for tc in (resp.tool_calls or [])]})
            return resp

        except Exception as e:
            _logger_llm.error("llm error model=%s %s", params.get('model', ''), e)
            return False

    def llm_responses(self, input_items, tools, model=None):
        """
        Call the OpenAI Responses API (not Completions) with the given input and tools.
        Returns a dict compatible with inca openai_adapter: {"output_text": str, "output": list}.
        """
        if self.AI_2 is None:
            return {"output_text": "", "output": []}
        try:
            params = {
                "model": model or self.AI_2_MODEL,
                "input": input_items,
                "tools": tools,
            }
            if not hasattr(self.AI_2, "responses"):
                return {"output_text": "", "output": []}
            response = self.AI_2.responses.create(**params)
            output_text_parts = []
            output_items = []
            output = getattr(response, "output", None) or []
            for item in output:
                content = getattr(item, "content", None) or []
                for c in content:
                    c_type = getattr(c, "type", None)
                    if c_type in ("message", "text") or hasattr(c, "text"):
                        text = getattr(c, "text", None)
                        if isinstance(text, str):
                            output_text_parts.append(text)
                    elif c_type == "tool_use":
                        output_items.append({
                            "type": "tool_call",
                            "id": getattr(c, "id", None),
                            "tool_call_id": getattr(c, "id", None),
                            "name": getattr(c, "name", None),
                            "arguments": getattr(c, "input", None) or {},
                        })
            return {
                "output_text": "\n".join(output_text_parts).strip() if output_text_parts else "",
                "output": output_items,
            }
        except Exception as e:
            _logger_llm.error("llm_responses_failed | %s", e)
            return {"output_text": "", "output": []}


    def new_chat_thread_document(self,public_user=''):
        """
        Check if thread exists and if not create new one

        """
        action = 'new_chat_thread_document'
        _logger_workspace.debug("new_chat_thread_document | running")

        try:
        # List threads
            threads = self.CHC.list_threads(self.portfolio,self.org,self.entity_type,self.entity_id)
            _logger_workspace.debug("list_threads | result=%s", threads.get('success'))

            if 'success' in threads:
                if len(threads['items']) < 1:
                    # No threads found
                    _logger_workspace.debug("creating_new_thread")
                    response_2 = self.CHC.create_thread(self.portfolio,self.org,self.entity_type, self.entity_id, public_user=public_user)

                    if not response_2.get('success'):
                        _logger_workspace.error("create_thread_failed | %s", response_2)
                        return {'success': False,'action': action,'input': '','output': response_2}

                    thread = response_2['document']

                else:
                    thread = threads['items'][0]
                return {
                    'success': True,'action': action,'output': thread
                }

            else:
                return {
                    'success': False,'action': action,'output': thread
                }

        except Exception as e:
            _logger_workspace.error("get_or_create_thread_failed | %s", e)
            return {'success': False,'action': action,'output': f"{e}"}

    def new_chat_message_document(self, message, public_user=None, next=None):
        """
        Create a new chat message document.

        Args:
            message (str): The message content
            public_user (str): The public user identifier

        Returns:
            dict: Success status and response
        """
        action = 'new_chat_message_document'
        _logger_workspace.debug("new_chat_message_document | running")

        try:

            message_context = {}
            message_context['portfolio'] = self.portfolio
            message_context['org'] = self.org
            message_context['public_user'] = public_user
            message_context['entity_type'] = self.entity_type
            message_context['entity_id'] = self.entity_id
            message_context['thread'] = self.thread

            new_message = {"role": "user", "content": message}
            msg_wrap = {
                "_out": new_message,
                "_type": "text",
                "_next": next
            }

            # Append new message to permanent storage
            message_object = {}
            message_object['context'] = message_context
            message_object['messages'] = [msg_wrap]

            response = self.CHC.create_turn(
                self.portfolio,
                self.org,
                self.entity_type,
                self.entity_id,
                self.thread,
                message_object
            )

            '''
            response format

            {
                "success":BOOL,
                "message": STRING,
                "document": {
                    'author_id': STRING,
                    'time': STRING,
                    'is_active': BOOL,
                    'context': DICT,
                    'messages': STRING,
                    'index': STRING,
                    'entity_index': STRING,
                    '_id': STRING
                },
                "status" : STRING
            }

            '''

            if 'document' in response and '_id' in response['document']:
                self.chat_id = response['document']['_id']

            _logger_workspace.debug("new_chat_message_document | response=%s", response.get('success'))

            if 'success' not in response:
                return {'success': False, 'action': action, 'input': message, 'output': response}

            return {'success': True, 'action': action, 'input': message, 'output': response['document']}

        except Exception as e:
            _logger_workspace.error("get_or_create_turn_failed | %s", e)
            return {'success': False,'action': action,'input': '','output': f"{e}"}

    def get_active_workspace(self, workspace_id=None):
        """
        Get the active workspace.

        Args:
            workspace_id (str): The workspace ID to get

        Returns:
            dict: The workspace or False if not found
        """
        workspaces_list = self.CHC.list_workspaces(
            self.portfolio,
            self.org,
            self.entity_type,
            self.entity_id,
            self.thread
        )

        if not workspaces_list['success']:
            return False

        if len(workspaces_list['items']) == 0:
            return False

            # Create a workspace as none exist
            payload = {}
            response = self.CHC.create_workspace(
                self.portfolio,
                self.org,
                self.entity_type,
                self.entity_id,
                self.thread, payload
            )
            if not response['success']:
                return False
            # Regenerate workspaces_list
            workspaces_list = self.CHC.list_workspaces(
                self.portfolio,
                self.org,
                self.entity_type,
                self.entity_id,
                self.thread
            )

        if not workspace_id:
            workspace = workspaces_list['items'][-1]
        else:
            for w in workspaces_list['items']:
                if w['_id'] == workspace_id:
                    workspace = w
                    break
            else:
                return False

        # Sanitize workspace before returning to ensure no Decimals are present
        return self.sanitize(workspace)

    def sanitize(self, obj):
        """
        Sanitize an object for JSON serialization.

        Args:
            obj: The object to sanitize

        Returns:
            The sanitized object
        """
        if isinstance(obj, list):
            return [self.sanitize(x) for x in obj]
        elif isinstance(obj, dict):
            return {k: self.sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, Decimal):
            # Convert Decimal to int if it's a whole number, otherwise float
            return int(obj) if obj % 1 == 0 else float(obj)
        elif isinstance(obj, float):
            # Convert float to string
            return str(obj)
        elif isinstance(obj, int):
            # Keep integers as is
            return obj
        else:
            return obj

    def prune_history(self, history):
        """
        Prunes the history list to keep only the most recent value for each key while maintaining chronological order.
        Objects at the bottom of the list are newer.

        Args:
            history (list): List of belief objects with key, val, time, and type fields

        Returns:
            list: Pruned history list with only the most recent value for each key
        """
        # Create a dictionary to track the most recent value for each key
        latest_values = {}

        # First pass: identify the most recent value for each key
        for item in history:
            key = item['key']
            latest_values[key] = item

        # Second pass: create new list maintaining original order but only including latest values
        pruned_history = []
        seen_keys = set()

        # Iterate through history in reverse to maintain chronological order
        for item in reversed(history):
            key = item['key']
            if key not in seen_keys:
                pruned_history.append(item)
                seen_keys.add(key)

        # Reverse back to maintain original order (newest at bottom)
        return list(reversed(pruned_history))

    def string_from_object(self, object: dict) -> str:
        """
        Converts a dictionary into a formatted string.

        Args:
            object (dict): Dictionary containing key-value pairs

        Returns:
            str: Formatted string with key-value pairs separated by commas

        Example:
            Input: {"origin": "NYC", "destination": "SF", "departure_date": "2025-06-20", "guest_count": 4}
            Output: "origin = NYC, destination = SF, departure_date = 2025-06-20, guest_count = 4"
        """
        if not object:
            return ""

        formatted_pairs = []
        for key, value in object.items():
            # Convert value to string and handle different types appropriately
            if isinstance(value, (int, float)):
                formatted_value = str(value)
            elif isinstance(value, str):
                formatted_value = value
            else:
                formatted_value = str(value)

            formatted_pairs.append(f"{key} = {formatted_value}")

        return ", ".join(formatted_pairs)

    def format_object_to_slash_string(self, obj: dict) -> str:
        """
        Converts an object into a string with values separated by slashes.
        If a value is not a string, it will be replaced with an empty space.
        Keys are sorted alphabetically to ensure consistent output regardless of input order.

        Args:
            obj (dict): Dictionary containing key-value pairs

        Returns:
            str: Formatted string with values separated by slashes

        Example:
            Input: {"people": "4", "time": "16:00", "date": "2025-06-04"}
            Output: "2025-06-04/4/16:00"
        """
        if not obj:
            return ""

        values = []
        # Sort keys alphabetically
        for key in sorted(obj.keys()):
            value = obj[key]
            if isinstance(value, str):
                values.append(value)
            else:
                values.append("")

        return "/".join(values)

    def clean_json_response(self, response):
        """
        Cleans and validates a JSON response string from LLM.

        Args:
            response (str): The raw JSON response string from LLM

        Returns:
            dict: The parsed JSON object if successful
            None: If parsing fails

        Raises:
            json.JSONDecodeError: If the response cannot be parsed as JSON
        """
        try:
            # Clean the response by ensuring property names are properly quoted
            cleaned_response = response
            # Remove any comments (both single-line and multi-line)
            cleaned_response = re.sub(r'//.*?$', '', cleaned_response, flags=re.MULTILINE)  # Remove single-line comments
            cleaned_response = re.sub(r'/\*.*?\*/', '', cleaned_response, flags=re.DOTALL)  # Remove multi-line comments

            # First try to parse as is
            try:
                return json.loads(cleaned_response)
            except json.JSONDecodeError:
                pass

            # If that fails, try to fix common issues
            # Handle unquoted property names at the start of the object
            cleaned_response = re.sub(r'^\s*{\s*(\w+)(\s*:)', r'{"\1"\2', cleaned_response)

            # Handle unquoted property names after commas
            cleaned_response = re.sub(r',\s*(\w+)(\s*:)', r',"\1"\2', cleaned_response)

            # Handle unquoted property names after newlines
            cleaned_response = re.sub(r'\n\s*(\w+)(\s*:)', r'\n"\1"\2', cleaned_response)

            # Replace single quotes with double quotes for property names
            cleaned_response = re.sub(r'([{,]\s*)\'(\w+)\'(\s*:)', r'\1"\2"\3', cleaned_response)

            # Replace single quotes with double quotes for string values
            # This regex looks for : 'value' pattern and replaces it with : "value"
            cleaned_response = re.sub(r':\s*\'([^\']*)\'', r': "\1"', cleaned_response)

            # Remove spaces between colons and boolean values
            cleaned_response = re.sub(r':\s+(true|false|True|False)', r':\1', cleaned_response)

            # Remove trailing commas in objects and arrays
            # This regex will match a comma followed by whitespace and then a closing brace or bracket
            cleaned_response = re.sub(r',(\s*[}\]])', r'\1', cleaned_response)

            # Remove any timestamps in square brackets
            cleaned_response = re.sub(r'\[\d+\]\s*', '', cleaned_response)

            # Try to parse the cleaned response
            try:
                return json.loads(cleaned_response)
            except json.JSONDecodeError as e:
                _logger_workspace.error("clean_json_first_attempt_failed | %s", e)

                # If first attempt fails, try to fix the raw field specifically
                # Find the raw field and ensure it's properly formatted
                raw_match = re.search(r'"raw":\s*({[^}]+})', cleaned_response)
                if raw_match:
                    raw_content = raw_match.group(1)
                    # Convert single quotes to double quotes in the raw content
                    raw_content = raw_content.replace("'", '"')
                    # Replace the raw field with the cleaned version
                    cleaned_response = cleaned_response[:raw_match.start(1)] + raw_content + cleaned_response[raw_match.end(1):]

                return json.loads(cleaned_response)

        except json.JSONDecodeError as e:
            _logger_workspace.error("clean_json_parse_failed | %s", e)
            raise

    def _convert_to_dict(self, obj):
        """
        Recursively converts an OpenAI response object to a dictionary.

        Args:
            obj: The object to convert (can be ChatCompletionMessage, ChatCompletionMessageToolCall, etc.)

        Returns:
            dict: The converted dictionary
        """
        if hasattr(obj, '__dict__'):
            return {key: self._convert_to_dict(value) for key, value in obj.__dict__.items()}
        elif isinstance(obj, list):
            return [self._convert_to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {key: self._convert_to_dict(value) for key, value in obj.items()}
        else:
            return obj

    def remove_outer_escape(self, double_escaped_string):
        """
        Removes the outer escape from a double-escaped JSON string.

        Args:
            double_escaped_string (str): A string that has been escaped twice

        Returns:
            str: The cleaned JSON string, or None if parsing fails

        Example:
            Input: '"{\\"raw_document\\":\\"Wood Property\\\\n1/25 Wellington Street...\\"}"'
            Output: '{"raw_document": "Wood Property\n1/25 Wellington Street..."}'
        """
        try:
            # First, parse the outer JSON string to get the inner escaped string
            outer_parsed = json.loads(double_escaped_string)

            # Then parse the inner string to get the actual JSON object
            if isinstance(outer_parsed, str):
                inner_parsed = json.loads(outer_parsed)
                # Return the cleaned JSON as a string
                return json.dumps(inner_parsed)
            else:
                # If it's already a dict, convert back to string
                return json.dumps(outer_parsed)

        except json.JSONDecodeError as e:
            _logger_workspace.error("remove_outer_escape_failed | %s", e)
            return None

    def validate_interpret_openai_llm_response(self, raw_response: dict) -> dict:
        """
        Validates that the LLM response follows the expected format.

        Args:
            raw_response (dict): The raw response from the LLM

        Returns:
            dict: Validation result with success status and output
        """
        action = 'validate_interpret_openai_llm_response'
        # Convert OpenAI response object to dictionary if needed
        response = self._convert_to_dict(raw_response)

        # Check if response has required 'role' field
        if 'role' not in response:
            return {"success": False, "action": action, "input": response, "output": "Response missing required 'role' field"}

        # Check if role is 'assistant'
        if response['role'] != 'assistant':
            return {"success": False, "action": action, "input": response, "output": "Response role must be 'assistant'"}

        # Check if response has either 'content' or 'tool_calls'
        has_content = 'content' in response and response['content'] is not None
        has_tool_calls = 'tool_calls' in response and response['tool_calls'] is not None

        if not (has_content or has_tool_calls):
            return {"success": False, "action": action, "input": response, "output": "Response must have either non-null 'content' or non-null 'tool_calls'"}

        if has_content and has_tool_calls:
            # If this happens, remove content so the message is still compliant
            response['content'] = ''

        # If it's a tool call, validate the tool_calls structure
        if has_tool_calls:
            if not isinstance(response['tool_calls'], list):
                return {"success": False, "action": action, "input": response, "output": "'tool_calls' must be a list"}

            for tool_call in response['tool_calls']:
                if not isinstance(tool_call, dict):
                    return {"success": False, "action": action, "input": response, "output": "Each tool call must be a dictionary"}

                required_fields = ['id', 'type', 'function']
                for field in required_fields:
                    if field not in tool_call or tool_call[field] is None:
                        return {"success": False, "action": action, "input": response, "output": f"Tool call missing required field '{field}' or field is null"}

                if tool_call['type'] != 'function':
                    return {"success": False, "action": action, "input": response, "output": "Tool call type must be 'function'"}

                if not isinstance(tool_call['function'], dict):
                    return {"success": False, "action": action, "input": response, "output": "Tool call 'function' must be a dictionary"}

                function_required_fields = ['name', 'arguments']
                for field in function_required_fields:
                    if field not in tool_call['function'] or tool_call['function'][field] is None:
                        return {"success": False, "action": action, "input": response, "output": f"Tool call function missing required field '{field}' or field is null"}

                # Validate that arguments is a valid JSON string
                try:
                    if isinstance(tool_call['function']['arguments'], str):
                        json.loads(tool_call['function']['arguments'])
                except json.JSONDecodeError:
                    # Try to fix double-escaped JSON
                    escape_result = self.remove_outer_escape(tool_call['function']['arguments'])
                    if escape_result:
                        # Validate the cleaned result
                        try:
                            json.loads(escape_result)
                            tool_call['function']['arguments'] = escape_result
                        except json.JSONDecodeError:
                            return {"success": False, "action": action, "input": response, "output": "Tool call arguments must be a valid JSON string after cleaning"}
                    else:
                        return {"success": False, "action": action, "input": response, "output": "Tool call arguments must be a valid JSON string"}

        # If it's a content response, validate content is a string
        if has_content and not isinstance(response['content'], str):
            return {"success": False, "action": action, "input": response, "output": "Content must be a string"}

        # Never persist str(None); some SDK paths leave content unset next to tool_calls.
        if response.get("role") == "assistant":
            c_raw = response.get("content")
            if c_raw is None or (
                isinstance(c_raw, str) and c_raw.strip() in ("None", "null")
            ):
                response["content"] = ""

        return {"success": True, "action": action, "output": response}

    def clear_tool_message_content(self, message_list, recent_tool_messages=1):
        """
        Clear content from all tool messages except the last x ones.
        This prevents overwhelming the LLM with tool output history.

        Args:
            message_list: List of messages to process
            recent_tool_messages: Number of recent tool messages to keep with content (default: 1)

        Returns:
            list: The processed message list
        """

        # Find the indices of the last x tool messages
        tool_indices = []
        for i in range(len(message_list) - 1, -1, -1):
            if message_list[i].get('role') == 'tool':
                tool_indices.append(i)
                if len(tool_indices) >= recent_tool_messages:
                    break

        # Only mutate tool-role messages. Assistant/user must keep content as returned by the API
        # (otherwise None becomes str(None) == "None" and poisons the next LLM call).
        for i, message in enumerate(message_list):
            if message.get('role') != 'tool':
                continue
            if i not in tool_indices:
                message['content'] = ""
                continue
            # Convert complex tool payload to string for OpenAI API
            if isinstance(message.get('content'), list):
                sanitized_content = self.sanitize(message['content'])
                message['content'] = json.dumps(sanitized_content)
            elif isinstance(message.get('content'), dict):
                sanitized_content = self.sanitize(message['content'])
                message['content'] = json.dumps(sanitized_content)
            else:
                sanitized_content = self.sanitize(message.get('content', ''))
                message['content'] = (
                    "" if sanitized_content is None else str(sanitized_content)
                )

        #print(f'Cleared tool message content: {message_list}') (verbose)

        return message_list

    def pre_process_message(self, message, list_actions=[]):
        """
        Combined function that processes a message through multiple stages in a single LLM call:
        1. Perception and interpretation
        2. Information processing
        3. Fact extraction
        4. Desire detection
        5. Action matching
        """
        action = 'pre_process_message'
        self.print_chat('Pre-processing message...', 'transient')

        try:
            # Get current time and date
            current_time = datetime.now().strftime("%Y-%m-%d")

            dict_actions = {}
            for a in list_actions:
                dict_actions[a['key']] = {
                    'goal': a.get('goal', ''),
                    'key': a.get('key', ''),
                    'utterances': a.get('utterances', ''),
                    'slots': a.get('slots', '')
                }

            # Get current workspace
            workspace = self.get_active_workspace()
            current_action = workspace.get('state', {}).get('action', '') if workspace else ''
            last_belief = workspace.get('state', {}).get('belief', {}) if workspace else {}
            belief_history = workspace.get('state', {}).get('history', []) if workspace else []

            # Clean and prepare belief history if provided
            cleaned_belief_history = self.sanitize(belief_history) if belief_history else []
            pruned_belief_history = self.prune_history(cleaned_belief_history) if cleaned_belief_history else []
            prompt_text = f"""
            You are a comprehensive message processing module for a BDI agent. Your task is to process a user message through multiple stages in a single pass.

            STAGE 1 - PERCEPTION AND INTERPRETATION:
            Extract structured information from the raw message:
            - Identify the user's intent
            - Extract key entities mentioned
            - Note any tools that might be needed
            - For each entity detected, create a belief history entry with:
            * type: "belief"
            * key: entity name
            * val: entity value
            * time: current timestamp

            STAGE 2 - INFORMATION PROCESSING:
            Enrich and normalize the extracted information:
            - Normalize values (e.g., convert "tomorrow" to full date)
            - Add derived information
            - Validate and standardize formats
            - Compare available beliefs with the slots required by the matched action
            - Identify missing beliefs by:
            * Checking each required slot from the matched action
            * Verifying if we have corresponding values in current beliefs
            * Considering both exact matches and semantic equivalents
            * Including slots that are required but not yet provided
            - Track missing beliefs that are essential for completing the current task

            STAGE 3 - FACT EXTRACTION:
            From the belief history, extract the most up-to-date facts:
            - Use the most recent value for each key
            - Combine with newly extracted information
            - Maintain chronological order

            STAGE 4 - DESIRE DETECTION:
            Analyze the combined information to determine the user's goal:
            - Consider the current action: {current_action}
            - Review the entire belief history to understand the ongoing conversation context
            - Consider the chronological progression of user's statements and preferences
            - Only change the previously detected desire if:
            * The new message explicitly states a different intention
            * The new message provides critical information that fundamentally changes the goal
            * The user explicitly requests to change their previous intention
            - If the new message only adds facts without changing intent, maintain the previous desire
            - Summarize the user's desire in a natural language sentence
            - Focus on the primary objective that has been consistent throughout the conversation

            STAGE 5 - ACTION MATCHING:
            Match the processed information with available actions:
            - Consider the current action: {current_action}
            - Only change the current action if:
            * The new message explicitly requests a different action
            * The new message's intent clearly conflicts with the current action
            * The user explicitly states they want to do something else
            - If the new message only adds information without changing intent:
            * Keep the current action
            * Use the message to fill missing slots
            * Update any relevant beliefs
            - Compare intent and beliefs with action descriptions
            - Consider the full belief history when matching
            - Select the most appropriate action
            - Provide confidence score

            Today's date is {current_time}

            ### Available Actions:
            {json.dumps(dict_actions, indent=2)}

            ### Current Belief:
            {json.dumps(last_belief, indent=2) if last_belief else "{}"}

            ### Belief History:
            {json.dumps(pruned_belief_history, indent=2) if pruned_belief_history else "[]"}

            ### User Message:
            {message}

            Return a JSON object with the following structure:
            {{
                "perception": {{
                    "intent": "string",
                    "entities": {{}},
                    "raw_text": "string",
                    "needs_tools": []
                }},
                "processed_info": {{
                    "enriched_entities": {{}},
                    "missing_beliefs": [],
                    "normalized_values": {{}}
                }},
                "facts": {{
                    // Key-value pairs of extracted facts
                }},
                "desire": "string",
                "action_match": {{
                    "confidence": 0-100,
                    "action": "string" // Use the key of the action,
                    "reasoning": "string",
                    "action_changed": boolean,
                    "change_reason": "string"
                }},
                "belief_history_updates": [
                    {{
                        "type": "belief",
                        "key": "string",
                        "val": "any",
                        "time": "ISO timestamp"
                    }}
                ]
            }}

            IMPORTANT RULES:
            1. Always use the most recent value for each fact
            2. Maintain all original information while enriching it
            3. Provide clear reasoning for action matching
            3b. Use the action key to indicate what action has been selected.
            4. Return valid JSON with all strings properly quoted
            5. For each new entity detected, create a belief history entry
            6. Use the belief history to inform action matching
            7. Include timestamps in ISO format for belief history entries
            8. Consider historical context when matching actions
            9. Only change the current action when explicitly requested or necessary
            10. Use new information to fill missing slots in the current action
            """
            prompt = {
                "model": self.AI_1_MODEL,
                "messages": [{ "role": "user", "content": prompt_text}],
                "temperature":0
            }
            response = self.llm(prompt)

            if not response.content:
                raise Exception('LLM response is empty')

            #print(f'PROCESS MESSAGE PROMPT >> {prompt}')
            result = self.clean_json_response(response.content)
            sanitized_result = self.sanitize(result)

            # Update workspace with the results
            if 'facts' in sanitized_result:
                self.mutate_workspace({'belief': sanitized_result['facts']})

            if 'desire' in sanitized_result:
                self.mutate_workspace({'desire': sanitized_result['desire']})

            if 'action_match' in sanitized_result and 'action' in sanitized_result['action_match']:
                # Check if action.key is used instead of action.name
                self.mutate_workspace({'action': sanitized_result['action_match']['action']})

            # Update belief history with new entities
            if 'belief_history_updates' in sanitized_result:
                for update in sanitized_result['belief_history_updates']:
                    self.mutate_workspace({'belief_history': {update['key']: update['val']}})

            #self.print_chat(sanitized_result, 'json')

            return {
                'success': True,
                'action': action,
                'input': message,
                'output': sanitized_result
            }

        except Exception as e:
            _logger_workspace.error("pre_process_message_failed | %s", e)
            # Only print raw response if it exists

            return {
                'success': False,
                'action': action,
                'input': message,
                'output': str(e)
            }

    def interpret(self, no_tools=False, list_actions=[], list_tools=[]):

        action = 'interpret'
        self.print_chat('Interpreting message...', 'transient')

        try:
            # We get the message history directly from the source of truth to avoid missing tool id calls.
            message_list = self.get_message_history()

            #print(f'Raw Message History: {message_list}')

            # Go through the message_list and replace the value of the 'content' attribute with an empty object when the role is 'tool'
            # Unless the last message it a tool response which the interpret function needs to process.
            # The reason is that we don't want to overwhelm the LLM with the contents of the history of tool outputs.

            # Clear content from all tool messages except the last one
            message_list = self.clear_tool_message_content(message_list['output'])

            #print(f'Cleared Message History: {message_list}')

            # Get current time and date
            current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

            # Workspace
            workspace = self.get_active_workspace()

            # Action
            current_action = workspace.get('state', {}).get('action', '') if workspace else ''

            action_instructions = ''
            action_tools = ''

            for a in list_actions:
                if a['key'] == current_action:
                    action_instructions = a['prompt_3_reasoning_and_planning']
                    if 'tools_reference' in a and a['tools_reference'] and a['tools_reference'] not in ['_','-','.']:
                        action_tools = a['tools_reference']
                    break

            # Belief
            current_beliefs = workspace.get('state', {}).get('beliefs', {}) if workspace else {}
            belief_str = 'Current beliefs: ' + self.string_from_object(current_beliefs)
            if workspace and workspace.get('intent'):
                belief_str += ' Current intent: ' + self.string_from_object(workspace['intent'])

            #belief_history = workspace.get('state', {}).get('history', []) if workspace else []
            #cleaned_belief_history = self.sanitize(belief_history) if belief_history else []
            #pruned_belief_history = self.prune_history(cleaned_belief_history) if cleaned_belief_history else []

            # Desire
            current_desire = workspace.get('state', {}).get('desire', '') if workspace else ''

            # Meta Instructions
            meta_instructions = {}
            # Language directive (highest priority)
            lang = str(self.config.get("AGENT_LANGUAGE", "pt-BR") or "pt-BR")
            if lang.lower().startswith("en"):
                meta_instructions["language_directive"] = (
                    "IMPORTANT: You MUST respond ONLY in English in all your messages to the user. "
                )
            else:
                meta_instructions["language_directive"] = (
                    "IMPORTANTE: Você DEVE responder APENAS em Português do Brasil em todas as suas mensagens ao usuário. "
                    "Isso se aplica a toda resposta, pergunta, esclarecimento e mensagem de erro que você produzir. "
                )
            # Initial instructions
            meta_instructions['opening_message'] = "You are an AI assistant. You can reason over conversation history, beliefs, and goals."
            # Provide the current time
            meta_instructions['current_time'] = f'The current time is: {current_time}'
            # Message to answer questions from the belief system
            meta_instructions['answer_from_belief'] = "You can reason over the message history and known facts (beliefs) to answer user questions. If the user asks a question, check the history or beliefs before asking again."

            # Message array
            messages = [
                { "role": "system", "content": meta_instructions["language_directive"]}, # LANGUAGE DIRECTIVE
                { "role": "system", "content": meta_instructions['opening_message']}, # META INSTRUCTIONS
                { "role": "system", "content": meta_instructions['current_time']}, # CURRENT TIME
                { "role": "system", "content": action_instructions}, # CURRENT ACTIONS
                { "role": "system", "content": belief_str }, # BELIEF SYSTEM
                { "role": "system", "content": meta_instructions['answer_from_belief']}
            ]

            # Add the incoming messages
            for msg in message_list:
                messages.append(msg)

            # Initialize approved_tools with default empty list
            approved_tools = []

            # Request asking the recommended tools for this action
            if action_tools and not no_tools:
                messages.append({ "role": "system", "content":f'In case you need them, the following tools are recommended to execute this action: {json.dumps(action_tools)}'})

                approved_tools = [tool.strip() for tool in action_tools.split(',')]

            # Tools
            '''
            tool.input should look like this in the database:

                {
                    "origin": {
                        "type": "string",
                        "description": "The departure city code or name",
                        "required":true
                    },
                    "destination": {
                        "type": "string",
                        "description": "The arrival city code or name",
                        "required":true
                    }
                }
            '''

            if no_tools:
                available_tools = None

            else:
                available_tools_raw = list_tools


                available_tools = []
                for t in available_tools_raw:

                    attrs = t.get('attributes', {}) if isinstance(t, dict) else {}
                    tool_key = t.get('key') or attrs.get('key')
                    tool_goal = t.get('goal') or attrs.get('goal', '')
                    tool_input_raw = t.get('input') or attrs.get('input', '[]')

                    if tool_key in approved_tools:
                        # Parse the escaped JSON string into a Python object
                        try:
                            tool_input = json.loads(tool_input_raw)
                        except json.JSONDecodeError:
                            _logger_workspace.error("invalid_tool_input_json | tool=%s", tool_key or 'unknown')
                            tool_input = []

                        dict_params = {}
                        required_params = []

                        # Handle new format: array of objects with name, hint, required
                        if isinstance(tool_input, list):
                            for param in tool_input:
                                if isinstance(param, dict) and 'name' in param and 'hint' in param:
                                    param_name = param['name']
                                    param_hint = param['hint']
                                    param_required = param.get('required', False)

                                    dict_params[param_name] = {
                                        'type': 'string',
                                        'description': param_hint
                                    }

                                    if param_required:
                                        required_params.append(param_name)
                        # Handle old format for backward compatibility
                        elif isinstance(tool_input, dict):
                            for key, val in tool_input.items():
                                dict_params[key] = {'type': 'string', 'description': val}
                                required_params.append(key)

                        _logger_workspace.debug("interpret | required_params=%s", required_params)

                        tool = {
                            'type': 'function',
                            'function': {
                                'name': tool_key or '',
                                'description': tool_goal,
                                'parameters': {
                                    'type': 'object',
                                    'properties': dict_params,
                                    'required': required_params
                                }
                            }
                        }

                        available_tools.append(tool)

            # Prompt
            prompt = {
                    "model": self.AI_1_MODEL,
                    "messages": messages,
                    "tools": available_tools,
                    "temperature":0,
                    "tool_choice": "auto"
                }

            prompt = self.sanitize(prompt)
            #print(f'RAW PROMPT >> {prompt}')
            response = self.llm(prompt)
            #print(f'RAW RESPONSE >> {response}')

            if not response:
                return {
                    'success': False,
                    'action': action,
                    'input': '',
                    'output': response
                }

            validation = self.validate_interpret_openai_llm_response(response)
            if not validation['success']:
                return {
                    'success': False,
                    'action': action,
                    'input': response,
                    'output': validation
                }

            validated_result = validation['output']

            # Saving : A) The tool call, or B) The message to the user
            self.save_chat(validated_result)


            return {
                'success': True,
                'action': action,
                'input': prompt,
                'output': validated_result
            }

        except Exception as e:
            _logger_workspace.error("interpret_failed | %s", e)
            return {
                'success': False,
                'action': action,
                'input': '',
                'output': str(e)
            }

    ## Execution of Intentions
    def act(self,plan,list_tools=[]):
        action = 'act'

        list_handlers = {}
        for t in list_tools_raw:
            list_handlers[t.get('key', '')] = t.get('handler', '')

        self._update_context(list_handlers=list_handlers)

        """Execute the current intention and return standardized response"""
        try:

            tool_name = plan['tool_calls'][0]['function']['name']
            params = plan['tool_calls'][0]['function']['arguments']
            if isinstance(params, str):
                params = json.loads(params)
            tid = plan['tool_calls'][0]['id']


            if not tool_name:
                raise ValueError("❌ No tool name provided in tool selection")

            self.print_chat(f'Calling tool {tool_name} with parameters {params} ', 'transient')

            # Check if handler exists
            if tool_name not in list_handlers:
                error_msg = f"❌ No handler found for tool '{tool_name}'"
                _logger_workspace.error("act_no_handler | tool=%s", tool_name)
                self.print_chat(error_msg, 'error')
                raise ValueError(error_msg)

            # Check if handler is an empty string
            if list_handlers[tool_name] == '':
                error_msg = f"❌ Handler is empty"
                _logger_workspace.error("act_empty_handler | tool=%s", tool_name)
                self.print_chat(error_msg, 'error')
                raise ValueError(error_msg)

            # Check if handler has the right format
            handler_route = list_handlers[tool_name]
            parts = handler_route.split('/')
            if len(parts) != 2:
                error_msg = f"❌ {tool_name} is not a valid tool."
                _logger_workspace.error("act_invalid_handler | tool=%s | route=%s", tool_name, handler_route)
                self.print_chat(error_msg, 'error')
                raise ValueError(error_msg)

            portfolio = self.portfolio
            org = self.org

            params['_portfolio'] = self.portfolio
            params['_org'] = self.org
            params['_entity_type'] = self.entity_type
            params['_entity_id'] = self.entity_id
            params['_thread'] = self.thread

            _logger_workspace.debug("act_calling | route=%s", handler_route)

            response = self.SHC.handler_call(portfolio,org,parts[0],parts[1],params)

            response_clean = {k: v for k, v in response.items() if k not in ['stack', 'output']}
            response_clean['output_size'] = len(str(response.get('output', '')))

            if not response['success']:
                return {'success':False,'action':action,'input':params,'output':response}

            # The response of every handler always comes nested
            clean_output = response['output']
            clean_output_str = json.dumps(clean_output, cls=DecimalEncoder)

            interface = None

            # The handler determines the interface
            if isinstance(response['output'], dict) and 'interface' in response['output']:
                interface = response['output']['interface']
            elif isinstance(response['output'], list) and len(response['output']) > 0 and 'interface' in response['output'][0]:
                interface = response['output'][0]['interface']

            tool_out = {
                    "role": "tool",
                    "tool_call_id": f'{tid}',
                    "content": clean_output_str,
                    "tool_calls":False
                }

            # Save the message after it's created
            if interface:
                self.save_chat(tool_out,interface=interface)

            else:
                self.save_chat(tool_out)


            # Results coming from the handler
            self._update_context(execute_intention_results=tool_out)


            # Save handler result to workspace

            # Turn an object like this one: {"people":"4","time":"16:00","date":"2025-06-04"}
            # Into a string like this one: "4/16:00/2026-06-04"
            # If the value of each key is not a string just output an empty space in its place
            #params_str = self.format_object_to_slash_string(params)
            index = f'irn:tool_rs:{handler_route}'
            tool_input = plan['tool_calls'][0]['function']['arguments']
            #input is a serialize json, you need to turn it into a python object before inserting it into the value dictionary
            tool_input_obj = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
            value = {'input': tool_input_obj, 'output': clean_output}
            self.mutate_workspace({'cache': {index:value}})


            _logger_workspace.debug("act_tool_execution_complete | tool=%s", tool_name)

            return {"success": True, "action": action, "input": plan, "output": tool_out}

        except Exception as e:

            error_msg = f"❌ Execute Intention failed. @act trying to run tool:'{tool_name}': {str(e)}"
            self.print_chat(error_msg,'error')
            _logger_workspace.error("act_failed | tool=%s | %s", tool_name, e)
            self._update_context(execute_intention_error=error_msg)

            error_result = {
                "success": False, "action": action,"input": plan,"output": str(e)
            }

            self._update_context(execute_intention_results=error_result)
            return error_result

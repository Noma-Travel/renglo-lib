#
from renglo.data.data_controller import DataController
from renglo.docs.docs_controller import DocsController
from renglo.chat.chat_controller import ChatController
from renglo.schd.schd_controller import SchdController

from renglo.agent.agent_utilities import AgentUtilities


from openai import OpenAI

import random
import json
import boto3
from datetime import datetime
from typing import List, Dict, Any, Callable
import re
from decimal import Decimal
from contextvars import ContextVar
from dataclasses import dataclass, field
import time
import uuid

@dataclass
class RequestContext:
    """Request-scoped context for agent operations."""
    connection_id: str = ''
    portfolio: str = ''
    org: str = ''
    public_user: str = ''
    entity_type: str = ''
    entity_id: str = ''
    thread: str = ''
    workspace_id: str = ''
    chat_id: str = ''
    workspace: Dict[str, Any] = field(default_factory=dict)
    belief: Dict[str, Any] = field(default_factory=dict)
    desire: str = ''
    action: str = ''
    plan: Dict[str, Any] = field(default_factory=dict)
    execute_intention_results: Dict[str, Any] = field(default_factory=dict)
    execute_intention_error: str = ''
    completion_result: Dict[str, Any] = field(default_factory=dict)
    list_handlers: Dict[str, Any] = field(default_factory=dict)
    message_history: List[Dict[str, Any]] = field(default_factory=list)
    list_actions: List[Dict[str, Any]] = field(default_factory=list)
    list_tools: List[Dict[str, Any]] = field(default_factory=list)

# Create a context variable to store the request context
request_context: ContextVar[RequestContext] = ContextVar('request_context', default=RequestContext())

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)

class AgentCore:
    def __init__(self, config=None):
        """
        Initialize AgentCore with configuration.
        
        Args:
            config (dict): Configuration dictionary containing API keys, URLs, etc.
        """
        self.config = config or {}
        
        self.DAC = DataController(config=self.config)
        self.DCC = DocsController(config=self.config)
        self.CHC = ChatController(config=self.config)
        self.SHC = SchdController(config=self.config)
        
        # AgentUtilities will be initialized in the run function
        self.AGU = None
        
        
       

    def _get_context(self) -> RequestContext:
        """Get the current request context."""
        return request_context.get()

    def _set_context(self, context: RequestContext):
        """Set the current request context."""
        request_context.set(context)

    def _update_context(self, **kwargs):
        """Update specific fields in the current request context."""
        context = self._get_context()
        for key, value in kwargs.items():
            setattr(context, key, value)
        self._set_context(context)
        
    def _get_utilities(self) -> AgentUtilities:
        """Get or create AgentUtilities instance with current context."""
        context = self._get_context()
        if not self.AGU or (self.AGU.portfolio != context.portfolio or 
                           self.AGU.org != context.org or 
                           self.AGU.entity_type != context.entity_type or 
                           self.AGU.entity_id != context.entity_id or 
                           self.AGU.thread != context.thread):
            self.AGU = AgentUtilities(
            self.config,
            context.portfolio,
            context.org,
            context.entity_type,
            context.entity_id,
            context.thread
            ) 
        return self.AGU
        

        



    # -------------------------------------------------------- LOOP FUNCTIONS
    
    
    def pre_process_message(self, message):
        """
        Combined function that processes a message through multiple stages in a single LLM call:
        1. Perception and interpretation
        2. Information processing
        3. Fact extraction
        4. Desire detection
        5. Action matching
        """
        action = 'pre_process_message'
        self.AGU.print_chat('Pre-processing message...', 'text', connection_id=self._get_context().connection_id)
        
        try:        
            # Get current time and date
            current_time = datetime.now().strftime("%Y-%m-%d")
            
            # Get available actions
            list_actions = self._get_context().list_actions
             
            dict_actions = {}
            for a in list_actions:
                dict_actions[a['key']] = {
                    'goal': a.get('goal', ''),
                    'key': a.get('key', ''),
                    'utterances': a.get('utterances', ''),
                    'slots': a.get('slots', '')
                }
            
            # Get current workspace
            workspace = self.AGU.get_active_workspace()
            current_action = workspace.get('state', {}).get('action', '') if workspace else ''
            last_belief = workspace.get('state', {}).get('belief', {}) if workspace else {}
            belief_history = workspace.get('state', {}).get('history', []) if workspace else []
                    
            # Clean and prepare belief history if provided
            cleaned_belief_history = self.AGU.sanitize(belief_history) if belief_history else []
            pruned_belief_history = self.AGU.prune_history(cleaned_belief_history) if cleaned_belief_history else []
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
                "model": self.AGU.AI_1_MODEL,
                "messages": [{ "role": "user", "content": prompt_text}],
                "temperature":0
            }
            response = self.AGU.llm(prompt)
            
            if not response.content:
                raise Exception('LLM response is empty')
                
            
            #print(f'PROCESS MESSAGE PROMPT >> {prompt}')
            result = self.AGU.clean_json_response(response.content)
            sanitized_result = self.AGU.sanitize(result)
            
            # Update workspace with the results
            if 'facts' in sanitized_result:
                self.AGU.mutate_workspace({'belief': sanitized_result['facts']}, public_user=self._get_context().public_user, workspace_id=self._get_context().workspace_id)
            
            if 'desire' in sanitized_result:
                self.AGU.mutate_workspace({'desire': sanitized_result['desire']}, public_user=self._get_context().public_user, workspace_id=self._get_context().workspace_id)
            
            if 'action_match' in sanitized_result and 'action' in sanitized_result['action_match']:
                # Check if action.key is used instead of action.name  
                self.AGU.mutate_workspace({'action': sanitized_result['action_match']['action']}, public_user=self._get_context().public_user, workspace_id=self._get_context().workspace_id)
            
            # Update belief history with new entities
            if 'belief_history_updates' in sanitized_result:
                for update in sanitized_result['belief_history_updates']:
                    self.AGU.mutate_workspace({'belief_history': {update['key']: update['val']}}, public_user=self._get_context().public_user, workspace_id=self._get_context().workspace_id)
            
            #self.print_chat(sanitized_result, 'json')
             
            return {
                'success': True,
                'action': action, 
                'input': message,
                'output': sanitized_result
            }
            
        except Exception as e:
            print(f"Error Pre-Processing message: {e}")
            # Only print raw response if it exists
            
            return {
                'success': False,
                'action': action,
                'input': message,
                'output': str(e)
            }
    
    
    
    def interpret(self,no_tools=False):
        
        action = 'interpret'
        self.AGU.print_chat('Interpreting message...', 'text', connection_id=self._get_context().connection_id)
        print('interpret')
        
        try:
            # We get the message history directly from the source of truth to avoid missing tool id calls. 
            message_list = self.AGU.get_message_history()
            
            #print(f'Raw Message History: {message_list}')
            
            # Go through the message_list and replace the value of the 'content' attribute with an empty object when the role is 'tool'
            # Unless the last message it a tool response which the interpret function needs to process. 
            # The reason is that we don't want to overwhelm the LLM with the contents of the history of tool outputs. 
            
            # Clear content from all tool messages except the last one
            message_list = self.AGU.clear_tool_message_content(message_list['output'])
            
            #print(f'Cleared Message History: {message_list}')
            
            
            # Get current time and date
            current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            
            # Workspace
            workspace = self.AGU.get_active_workspace()
            
            # Action  
            current_action = workspace.get('state', {}).get('action', '') if workspace else ''
            print(f'Current Action:{current_action}')
            
            action_instructions = '' 
            action_tools = ''
            list_actions = self._get_context().list_actions
            
            for a in list_actions:
                if a['key'] == current_action:
                    action_instructions = a['prompt_3_reasoning_and_planning']
                    if 'tools_reference' in a and a['tools_reference'] and a['tools_reference'] not in ['_','-','.']: 
                        action_tools = a['tools_reference']
                    break

            # Belief  
            current_beliefs = workspace.get('state', {}).get('beliefs', {}) if workspace else {}
            belief_str = 'Current beliefs: ' + self.AGU.string_from_object(current_beliefs)
            print(f'Current Belief:{belief_str}')
                
            #belief_history = workspace.get('state', {}).get('history', []) if workspace else []             
            #cleaned_belief_history = self.sanitize(belief_history) if belief_history else []
            #pruned_belief_history = self.prune_history(cleaned_belief_history) if cleaned_belief_history else []

            # Desire
            current_desire = workspace.get('state', {}).get('desire', '') if workspace else ''
            print(f'Current Desire:{current_desire}')
            
            # Meta Instructions
            meta_instructions = {}
            # Initial instructions
            meta_instructions['opening_message'] = "You are an AI assistant. You can reason over conversation history, beliefs, and goals."
            # Provide the current time
            meta_instructions['current_time'] = f'The current time is: {current_time}'
            # Message to answer questions from the belief system
            meta_instructions['answer_from_belief'] = "You can reason over the message history and known facts (beliefs) to answer user questions. If the user asks a question, check the history or beliefs before asking again."
                  
            # Message array
            messages = [
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
                list_tools = None      
                   
            else:         
                list_tools_raw = self._get_context().list_tools
                
                print(f'List Tools:{list_tools_raw}')
                
                list_tools = [] 
                for t in list_tools_raw:
                    attrs = t.get('attributes', {}) if isinstance(t, dict) else {}
                    tool_key = t.get('key') or attrs.get('key')
                    tool_goal = t.get('goal') or attrs.get('goal', '')
                    tool_input_raw = t.get('input') or attrs.get('input', '[]')
                    
                    if tool_key in approved_tools:
                        # Parse the escaped JSON string into a Python object
                        try:
                            tool_input = json.loads(tool_input_raw)
                        except json.JSONDecodeError:
                            print(f"Invalid JSON in tool input for tool {tool_key or 'unknown'}. Using empty array.")
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
                                
                        print(f'Required parameters:{required_params}')
                            
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
                        
                        #print(f'Tool:{tool}')       
                        list_tools.append(tool)          
                        #print(f'List Tools:{list_tools}')
                    
                    
            # Prompt
            prompt = {
                    "model": self.AGU.AI_1_MODEL,
                    "messages": messages,
                    "tools": list_tools,
                    "temperature":0,
                    "tool_choice": "auto"
                }
            
            
            prompt = self.AGU.sanitize(prompt)
            
            #print(f'RAW PROMPT >> {prompt}')
    
            response = self.AGU.llm(prompt)
            
            #print(f'RAW RESPONSE >> {response}')
          
            
            if not response:
                return {
                    'success': False,
                    'action': action,
                    'input': '',
                    'output': response
                }
                
            
            validation = self.AGU.validate_interpret_openai_llm_response(response)
            if not validation['success']:
                return {
                    'success': False,
                    'action': action,
                    'input': response,
                    'output': validation
                }
            
            validated_result = validation['output']
           
            # Saving : A) The tool call, or B) The message to the user
            self.AGU.save_chat(validated_result, connection_id=self._get_context().connection_id)  
 
                      
            return {
                'success': True,
                'action': action,
                'input': prompt,
                'output': validated_result
            }
            
        except Exception as e:
            print(f"Error in interpret() message: {e}")
            return {
                'success': False,
                'action': action,
                'input': '',
                'output': str(e)
            }
    
        
        
    ## Execution of Intentions
    def act(self,plan):
        action = 'act'
        
        list_tools_raw = self._get_context().list_tools
        
        list_handlers = {}
        for t in list_tools_raw:
            attrs = t.get('attributes', {}) if isinstance(t, dict) else {}
            key = t.get('key') or attrs.get('key')
            handler = t.get('handler') or attrs.get('handler', '')
            if key:
                list_handlers[key] = handler
            
        self._update_context(list_handlers=list_handlers)
    
        """Execute the current intention and return standardized response"""
        try:
            
            tool_name = plan['tool_calls'][0]['function']['name']
            params = plan['tool_calls'][0]['function']['arguments']
            if isinstance(params, str):
                params = json.loads(params)
            tid = plan['tool_calls'][0]['id']
            
            print(f'tid:{tid}')

            if not tool_name:
                raise ValueError("❌ No tool name provided in tool selection")
                
            print(f"Selected tool: {tool_name}")
            self.AGU.print_chat(f'Calling tool {tool_name} with parameters {params} ', 'text', connection_id=self._get_context().connection_id)
            print(f"Parameters: {params}")

            # Check if handler exists
            if tool_name not in list_handlers:
                error_msg = f"❌ No handler found for tool '{tool_name}'"
                print(error_msg)
                self.AGU.print_chat(error_msg, 'text', connection_id=self._get_context().connection_id)
                raise ValueError(error_msg)
            
            # Check if handler is an empty string
            if list_handlers[tool_name] == '':
                error_msg = f"❌ Handler is empty"
                print(error_msg)
                self.AGU.print_chat(error_msg, 'text', connection_id=self._get_context().connection_id)
                raise ValueError(error_msg)
                
            # Check if handler has the right format
            handler_route = list_handlers[tool_name]
            parts = handler_route.split('/')
            if len(parts) != 2:
                error_msg = f"❌ {tool_name} is not a valid tool."
                print(error_msg)
                self.AGU.print_chat(error_msg, 'text', connection_id=self._get_context().connection_id)
                raise ValueError(error_msg)
            

            portfolio = self._get_context().portfolio
            org = self._get_context().org
            
            params['_portfolio'] = self._get_context().portfolio
            params['_org'] = self._get_context().org
            params['_entity_type'] = self._get_context().entity_type
            params['_entity_id'] = self._get_context().entity_id
            params['_thread'] = self._get_context().thread
            
            print(f'Calling {handler_route} ') 
            
            response = self.SHC.handler_call(portfolio,org,parts[0],parts[1],params)
            
            print(f'Handler response:{response}')

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
                self.AGU.save_chat(tool_out,interface=interface,connection_id=self._get_context().connection_id)
                
            else:
                self.AGU.save_chat(tool_out,connection_id=self._get_context().connection_id)
                
                
            
            print(f'flag3')
            
            # Results coming from the handler
            self._update_context(execute_intention_results=tool_out)
            
            print(f'flag4')
            
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
            self.AGU.mutate_workspace({'cache': {index:value}}, public_user=self._get_context().public_user, workspace_id=self._get_context().workspace_id)
            
            print(f'flag5')
            
            #print(f'message output: {tool_out}')
            print("✅ Tool execution complete.")
            
            return {"success": True, "action": action, "input": plan, "output": tool_out}
                    
        except Exception as e:

            error_msg = f"❌ Execute Intention failed. @act trying to run tool:'{tool_name}': {str(e)}"
            self.AGU.print_chat(error_msg,'text', connection_id=self._get_context().connection_id) 
            print(error_msg)
            self._update_context(execute_intention_error=error_msg)
            
            error_result = {
                "success": False, "action": action,"input": plan,"output": str(e)    
            }
            
            self._update_context(execute_intention_results=error_result)
            return error_result
        
        
        
    
    
    
    
    
    
    
    # EXAMPLE: PROMPT TO EXTRACT BELIEFS 
    '''
    You are an AI assistant that extracts the user's intent and relevant information from a conversation.

    Below is the message history between the user and assistant. Your task is to return a list of beliefs — each belief is a key-value pair that represents something the user has stated or implied.

    Only extract **factual and relevant** information that could help accomplish a task. Do not include the assistant's own responses unless they reflect a confirmed fact.

    Use this JSON format:
    [
    { "key": "origin", "value": "São Paulo", "source": "user" },
    { "key": "destination", "value": "Recife", "source": "user" },
    { "key": "departure_date", "value": "2025-06-12", "source": "user" }
    ]

    If a value is unknown or missing, do not include it.

    Conversation:
    ---
    User: Quero um voo barato para Recife.
    Assistant: Claro! De onde você está saindo e para qual data?
    User: De São Paulo, dia 12 de junho.
    ---
        '''
        
        
        # EXAMPLE: PROMPT THAT SHOWS OPTIMAL FLOW
    '''
    `
            - answer in brazilian portuguese
            - you help users book flights!
            - keep your responses limited to a sentence.
            - DO NOT output lists.
            - after every tool call, pretend you're showing the result to the user and keep your response limited to a phrase.
            - today's date is ${new Date().toLocaleDateString()}.
            - ask follow up questions to nudge user into the optimal flow
            - ask for any details you don't know, like name of passenger, etc.'
            - C and D are aisle seats, A and F are window seats, B and E are middle seats
            - assume the most popular airports for the origin and destination
            - here's the optimal flow
            - search for flights
            - choose flight
            - select seats
            - create reservation (ask user whether to proceed with payment or change reservation)
            - authorize payment (requires user consent, wait for user to finish payment and let you know when done)
            - display boarding pass (DO NOT display boarding pass without verifying payment)
            '
        `
    '''
    
    
    
    def run(self,payload):
        # Initialize a new request context
        action = 'run > agent_core'
        print(f'Running: {action}')
        print(f'Payload: {payload}')  
        
        context = RequestContext()
        
        # Update context with payload data
        if 'connectionId' in payload:
            context.connection_id = payload["connectionId"]
                  
        if 'portfolio' in payload:
            context.portfolio = payload['portfolio']
        else:
            return {'success':False,'action':action,'input':payload,'output':'No portfolio provided'}
        
        if 'org' in payload:
            context.org = payload['org']
        else:
            context.org = '_all' #If no org is provided, we switch the Agent to portfolio level
            
        if 'public_user' in payload:
            context.public_user = payload['public_user']
                    
        if 'entity_type' in payload:
            context.entity_type = payload['entity_type']
        else:
            return {'success':False,'action':action,'input':payload,'output':'No entity_type provided'}
        
        if 'entity_id' in payload:
            context.entity_id = payload['entity_id']
        else:
            return {'success':False,'action':action,'input':payload,'output':'No entity_id provided'}
        
        if 'thread' in payload:
            context.thread = payload['thread']
        else:
            return {'success':False,'action':action,'input':payload,'output':'No thread provided'}
            
        if 'workspace' in payload:
            context.workspace_id = payload['workspace']
            
            
        self.AGU = AgentUtilities(
            self.config,
            context.portfolio,
            context.org,
            context.entity_type,
            context.entity_id,
            context.thread
            )
            
        # Get available actions and tools
        actions = self.DAC.get_a_b(context.portfolio, context.org, 'schd_actions')
        context.list_actions = actions['items']
        
        tools = self.DAC.get_a_b(context.portfolio, context.org, 'schd_tools')
        context.list_tools = tools['items']

        # Safety net: some orgs are created without schd_tools/schd_actions catalogs.
        # When that happens, tool selection succeeds but act() can't resolve handlers.
        # Auto-upload catalogs and reload before processing the first user message.
        if not context.list_tools or not context.list_actions:
            try:
                print("Catalog missing for org; running upload_tools_and_actions...")
                bootstrap_payload = {
                    "subhandler": "upload_all",
                    "mode": "reset",
                    "force_refresh": True
                }
                bootstrap = self.SHC.handler_call(
                    context.portfolio,
                    context.org,
                    "noma",
                    "upload_tools_and_actions",
                    bootstrap_payload
                )
                print(f"Catalog bootstrap result: {bootstrap}")
            except Exception as e:
                print(f"Catalog bootstrap failed: {e}")

            # Reload catalogs regardless of bootstrap outcome.
            actions = self.DAC.get_a_b(context.portfolio, context.org, 'schd_actions')
            context.list_actions = actions.get('items', [])
            tools = self.DAC.get_a_b(context.portfolio, context.org, 'schd_tools')
            context.list_tools = tools.get('items', [])
        
        # Set the initial context for this turn
        self._set_context(context)
        
        results = []
         
        # Get the initial chat message history and put it in the context
        message_history = self.AGU.get_message_history()
        if not message_history['success']:
            return {'success':False,'action':action,'output':message_history}
            
        # Update context with message history
        self._update_context(message_history=message_history['output'])
        #print(f'FULL message history:{message_history}')
           
        try:
            
            # Step 0: Create thread/message document
            response_0 = self.AGU.new_chat_message_document(payload['data'], public_user=context.public_user)
            results.append(response_0)
            if not response_0['success']: 
                return {'success':False,'action':action,'output':results}
             
            # Step 0b: Pre-process message
            response_0b = self.pre_process_message(payload['data'])
            results.append(response_0b)
            if not response_0b['success']: 
                return {'success':False,'action':action,'output':results}
            
            
            loops = 0
            loop_limit = 6
            while loops < loop_limit:
                loops = loops + 1
                print(f'Loop iteration {loops}/{loop_limit}')
                
                # Step 1: Interpret. We receive the message from the user and we issue a tool command or another message       
                response_1 = self.interpret()
                results.append(response_1)
                if not response_1['success']:
                    # Something went wrong during message interpretation
                    return {'success':False,'action':action,'output':results}  
                       
                
                # Check whether we need to run a tool
                if 'tool_calls' not in response_1['output'] or not response_1['output']['tool_calls']:
                    # No tool needs execution. 
                    # Most likely the agent is asking for more information to fill tool parameters. 
                    # Or agent is answering questions directly from the belief system.
                    self.AGU.print_chat(f'🤖','text', connection_id=context.connection_id)
                    return {'success':True,'action':action,'input':payload,'output':results}
                                
                else:
                    # Step 2: Act. Agent runs the tool
                    response_2 = self.act(response_1['output'])
                    results.append(response_2)
                    
                    
                    # Step 3: Check if answer needs to go out without LLM interpretation
                    #if 'direct_out' in response_2:
                        
                    if not response_2['success']:
                        # Something went wrong during tool execution
                        return {'success':False,'action':action,'output':results}
                    
                    
                    
            
            #Gracious exit. Analyze the last tool run (act()) but you can't issue a new tool_call. 
            response_1 = self.interpret(no_tools=True)
            results.append(response_1)
            if not response_1['success']:
                    # Something went wrong during message interpretation
                    return {'success':False,'action':action,'output':results} 
            
            
            # If we reach here, we hit the loop limit
            print(f'Warning: Reached maximum loop limit ({loop_limit})')
            #self.print_chat(f'🤖⚠️  Can you re-formulate your request please?','text')
            return {'success':True,'action':action,'input':payload,'output':results}
                    

            
        except Exception as e:
            self.AGU.print_chat(e,'text', connection_id=context.connection_id)
            self.AGU.print_chat(f'🤖❌','text', connection_id=context.connection_id)
            return {'success':False,'action':action,'message':f'Run failed. Error:{str(e)}','output':results}

    

# Test block
if __name__ == '__main__':
    # Creating an instance
    pass

    
    
    
 

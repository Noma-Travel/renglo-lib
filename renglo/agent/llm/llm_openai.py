from decimal import Decimal
from langfuse.openai import OpenAI
import json
import re


class LLMOpenAI:
    def __init__(self, config=None):
        """
        Initialize LLMOpenAI with configuration.
        
        Args:
            config (dict): Configuration dictionary containing OPENAI_API_KEY
        """
        self.config = config or {}
        
        # OpenAI Client 
        # **JUST FOR TESTING, THE REAL CLIENT WILL BE BEDROCK BASED**
        try:    
            openai_key = self.config.get('OPENAI_API_KEY', '')
            openai_client = OpenAI(api_key=openai_key)
            print(f"OpenAI client initialized")
        except Exception as e:
            print(f"Error initializing OpenAI client: {e}")
            openai_client = None
            
        self.AI_1 = openai_client
        #self.AI_1_MODEL = "gpt-4" // This model does not support json_object response format
        self.AI_1_MODEL = "gpt-3.5-turbo" # Baseline model. Good for multi-step chats
        self.AI_2_MODEL = "gpt-4o-mini" # This model is not very smart
        
    
    def call(self, prompt):
        
        # Create base parameters
        params = {
            'model': prompt['model'],
            'messages': prompt['messages'],
            'temperature': prompt['temperature']
        }
        
        try:
            print(f"[API] calling=openai_chat_completion | model={params.get('model')}")
        
            # Add optional parameters if they exist
            if 'tools' in prompt:
                params['tools'] = prompt['tools']
            if 'tool_choice' in prompt:
                params['tool_choice'] = prompt['tool_choice']
                
            #print(f'AS IS LLM INPUT:{params}')
                
            response = self.AI_1.chat.completions.create(**params)
            print("[API] response=openai_chat_completion | status=200")

            #print(f'AS IS LLM RESPONSE:{response}')
            
            return response.choices[0].message
 
        
        except Exception as e:
            print(f"Error running LLM call: {e}")
            # Only print raw response if it exists
            
            return {
                'success': False,
                'action': 'llm',
                'input': prompt,
                'output': str(e)
            }
            
    
    def sanitize(self,obj):
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
            #cleaned_response = response.strip() 
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
                print(f"First attempt failed. Error: {e}")
                #print(f"Cleaned response type: {type(cleaned_response)}")
                #print(f"Cleaned response length: {len(cleaned_response)}")
                #print(f"Cleaned response content: '{cleaned_response}'")
                
                # If first attempt fails, try to fix the raw field specifically
                # Find the raw field and ensure it's properly formatted
                raw_match = re.search(r'"raw":\s*({[^}]+})', cleaned_response)
                if raw_match:
                    raw_content = raw_match.group(1)
                    # Convert single quotes to double quotes in the raw content
                    raw_content = raw_content.replace("'", '"')
                    # Replace the raw field with the cleaned version
                    cleaned_response = cleaned_response[:raw_match.start(1)] + raw_content + cleaned_response[raw_match.end(1):]
                
                #print(f"After raw field cleanup - content: '{cleaned_response}'")
                return json.loads(cleaned_response)
        
                
        except json.JSONDecodeError as e:
            print(f"Error parsing cleaned JSON response: {e}")
            #print(f"Original response: {response}")
            #print(f"Cleaned response: {cleaned_response}")
            raise    
    
        
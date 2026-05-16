#data_controller.py
import urllib.parse
from datetime import datetime
import uuid
import re
import json, collections
import boto3
from decimal import Decimal

from renglo.data.data_model import DataModel
from renglo.blueprint.blueprint_controller import BlueprintController
from renglo.auth.auth_controller import AuthController
from renglo.search.search_index_service import SearchIndexService
from renglo.logger import get_logger
from renglo.logger import get_logger

# Add this custom JSON encoder class at the top level of your file
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(DecimalEncoder, self).default(obj)

def convert_js_to_json(js_string):
    """
    Convert JavaScript object syntax to proper JSON format.
    Handles unquoted property names and single quotes.
    """
    if not isinstance(js_string, str):
        return js_string


    # Replace single quotes with double quotes
    js_string = js_string.replace("'", '"')

    # More robust regex to handle nested objects and arrays
    # This pattern matches property names that aren't already quoted
    # and handles various whitespace scenarios
    import re

    # First, let's handle the most common case: simple property names
    # This regex looks for word characters that are followed by a colon
    # but not preceded by a quote
    pattern = r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:'
    replacement = r'\1"\2":'
    js_string = re.sub(pattern, replacement, js_string)

    # Handle property names at the start of the string (for root objects)
    pattern = r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:'
    replacement = r'"\1":'
    js_string = re.sub(pattern, replacement, js_string)


    return js_string

def convert_js_to_json_advanced(js_string):
    """
    More advanced JavaScript to JSON converter that handles complex cases.
    """
    if not isinstance(js_string, str):
        return js_string


    # Replace single quotes with double quotes
    js_string = js_string.replace("'", '"')

    import re

    # Handle property names in various contexts
    # This is a more comprehensive approach

    # Step 1: Handle property names after opening braces and commas
    js_string = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', js_string)

    # Step 2: Handle property names at the very beginning (for root objects)
    js_string = re.sub(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', js_string)

    # Step 3: Handle property names after array elements
    js_string = re.sub(r'([\]}])\s*,\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1,"\2":', js_string)


    return js_string

def convert_js_to_json_robust(js_string):
    """
    Robust JavaScript to JSON converter that handles whitespace, newlines, and formatting issues.
    """
    if not isinstance(js_string, str):
        return js_string


    # Replace single quotes with double quotes
    js_string = js_string.replace("'", '"')

    import re

    # Step 1: Handle property names in various contexts
    js_string = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', js_string)
    js_string = re.sub(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', js_string)
    js_string = re.sub(r'([\]}])\s*,\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1,"\2":', js_string)

    # Step 2: Clean up whitespace and formatting
    # Remove trailing commas before closing braces/brackets
    js_string = re.sub(r',(\s*[}\]])', r'\1', js_string)

    # Clean up excessive whitespace and newlines, but preserve time formats
    js_string = re.sub(r'\s+', ' ', js_string)

    # Clean up commas and colons, but be careful with time strings
    # Don't add spaces around colons in time strings (HH:MM:SS or HH:MM)
    js_string = re.sub(r'\s*,\s*', ', ', js_string)

    # Only add spaces around colons that are property separators, not time separators
    # This regex looks for colons that are followed by a space or closing brace/bracket
    js_string = re.sub(r'\s*:\s*(?=[^"]*["\d])', ': ', js_string)

    # Step 3: Ensure proper array/object formatting
    js_string = re.sub(r'\[\s*{', '[{', js_string)
    js_string = re.sub(r'}\s*\]', '}]', js_string)
    js_string = re.sub(r'}\s*,', '},', js_string)


    return js_string

def convert_js_to_json_simple(js_string):
    """
    Simple and reliable JavaScript to JSON converter.
    """
    if not isinstance(js_string, str):
        return js_string


    # Replace single quotes with double quotes
    js_string = js_string.replace("'", '"')

    import re

    # Step 1: Handle property names - only target property names, not values
    # Look for word characters followed by colon that are not inside quotes
    js_string = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', js_string)
    js_string = re.sub(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', js_string)

    # Step 2: Remove trailing commas
    js_string = re.sub(r',(\s*[}\]])', r'\1', js_string)

    # Step 3: Clean up excessive whitespace but preserve formatting
    js_string = re.sub(r'\s+', ' ', js_string)


    return js_string

class DataController:

    def __init__(self, config=None, tid=None, ip=None):
        self.config = config or {}
        self.logger = get_logger()
        self.DAM = DataModel(config=self.config, tid=tid, ip=ip)
        self.BPC = BlueprintController(config=self.config, tid=tid, ip=ip)
        self.AUC = AuthController(config=self.config, tid=tid, ip=ip)
        self.search_index = SearchIndexService(config=self.config)



    def refresh_s3_cache(self,portfolio, org, ring, sort=None):

        s3_client = boto3.client('s3')
        bucket_name = self.config.get('S3_BUCKET_NAME')
        if not bucket_name:
            raise ValueError("S3_BUCKET_NAME not found in config")
        self.logger.debug(f'Refreshing s3 cache')
        # Proceed to regenerate the document
        response = []  # Initialize response
        # Simulate regeneration logic
        max_iterations = 50
        limit = 249
        iterations = 0
        lastkey = None

        file_path = f'data/{portfolio}/{org}/{ring}'

        while True:
            iterations += 1
            self.logger.debug("Iteration:" + str(iterations))

            partial_response = self.get_a_b(portfolio, org, ring, limit, lastkey, sort)
            response.extend(partial_response['items'])
            lastkey = partial_response.get('last_id')

            if lastkey is None or iterations >= max_iterations:
                break

        result = {
            "items": response,
            "last_id": None,
            "success": True
        }

        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=file_path,
            Body=json.dumps(result, cls=DecimalEncoder)
        )

        return result, 201

    def sanitize(self,obj):
        '''
        Avoids Floats being sent to DynamoDB
        '''
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

    def generate_index_string_x(self,blueprint,item_values):
        # Check if blueprint has an "indexes" key
        indexes = blueprint.get('indexes')
        if indexes is None:
            return False  # No index string needs to be generated if "indexes" doesn't exist

        # Ensure "indexes" has a "path" key and it is a list
        path = indexes.get('path')
        if not isinstance(path, list):
            return False  # Invalid indexes object, exit with False

        # Check if all fields in the path exist in the blueprint's fields
        valid_fields = {field['name'] for field in blueprint.get('fields', [])}
        for field_name in path:
            if field_name not in valid_fields:
                # Log an error and exit gracefully
                print(f"Error: Field '{field_name}' does not exist in the blueprint.")
                return False

        # Start building the index string with the constant prefix
        index_string = "irn:h_index:"

        # Iterate through the path list and construct the index string
        for field_name in path:
            # Determine the resource name by removing the '_id' suffix
            resource_name = field_name.replace('_id', '')

            # Append resource name to the index string
            index_string += resource_name + ":"

            # Get the value of the field from item_values
            if field_name not in item_values:
                # Log an error and exit gracefully
                print(f"Error: Field '{field_name}' not found in item_values.")
                return False

            index_value = item_values[field_name]
            index_string += str(index_value) + ":"

        # Remove the trailing colon from the constructed index string
        index_string = index_string.rstrip(":")

        # Append the blueprint name (current resource name) to the index string
        index_string += f":{blueprint.get('name')}"

        # Check if there's a "time" key in the indexes dictionary

        time_fields = indexes.get('time', [])
        if isinstance(time_fields, list):
            safe_values = []

            # Compare and validate each field in the time list
            for time_field in time_fields:
                if time_field in valid_fields and time_field in item_values:
                    # Get the value from item_values
                    field_value = item_values[time_field]

                    # Make the field value safe by allowing only alphanumeric characters and replacing spaces with underscores
                    safe_value = re.sub(r'[^a-zA-Z0-9]', '_', field_value)
                    safe_values.append(safe_value)
                else:
                    safe_values.append('_')

            # If we have valid time field values, concatenate them
            if safe_values:
                # Join values with an underscore if more than one
                concatenated_values = ":".join(safe_values)

                # Get the current timestamp in Unix epoch (seconds)
                current_timestamp = int(datetime.now().timestamp())

                # Create the final timestamp string with field values and the current timestamp
                timestamp_string = f"{concatenated_values}:{current_timestamp}"

                # Concatenate the timestamp string to the index string with a dot
                index_string += f".{timestamp_string}"

        return index_string

    def generate_index_string(self,blueprint,org,item_values):
        # Check if blueprint has an "indexes" key
        indexes = blueprint.get('indexes')
        if indexes is None:
            return False  # No index string needs to be generated if "indexes" doesn't exist

        # Ensure "indexes" has a "path" key and it is a list
        path = indexes.get('path')
        if not isinstance(path, list):
            return False  # Invalid indexes object, exit with False

        # Check if all fields in the path exist in the blueprint's fields
        valid_fields = {field['name'] for field in blueprint.get('fields', [])}
        for field_name in path:
            if field_name not in valid_fields:
                # Log an error and exit gracefully
                print(f"Error: Field '{field_name}' does not exist in the blueprint.")
                return False

        # Start building the index string with the constant prefix
        index_string = "irn:h_index:"
        index_string += f"{org}:"
        index_string += f"{blueprint.get('name')}:"

        # Iterate through the path list and construct the index string
        for field_name in path:

            # Get the value of the field from item_values
            if field_name not in item_values:
                # Log an error and exit gracefully
                print(f"Error: Field '{field_name}' not found in item_values.")
                return False

            index_value = item_values[field_name]
            index_string += str(index_value) + ":"

        # Remove the trailing colon from the constructed index string
        index_string = index_string.rstrip(":")

        return index_string

    def construct_post_item(self,portfolio,org,ring,payload):
        '''
        Creates a new item following the blueprint fields and data submitted via the request.

        @IN:

          portfolio = (string)
          org = (string)
          ring= (string)
          payload = (dict)

        @OUT:
          ok:(item_id)
          ko:False

        @COLLATERAL:
          - Create new document in DB
          - Increase item count in userdoc

        '''

        version = 'last'

        blueprint = self.BPC.get_blueprint('irma',ring,version)

        if not isinstance(blueprint, dict) or blueprint.get('success') is False or 'fields' not in blueprint:
            missing = (
                blueprint.get('message', 'unknown')
                if isinstance(blueprint, dict)
                else 'not a blueprint document'
            )
            raise ValueError(
                f"Blueprint for ring '{ring}' not found or invalid ({missing}). "
                f"Expected irn:blueprint:irma:{ring} with a 'fields' array in {self.config.get('DYNAMODB_BLUEPRINT_TABLE', 'blueprints table')}."
            )

        item_values = {}
        #rich_values = {}
        #history_values = {}
        #flag_values = {}
        fields = blueprint['fields']

        self.logger.debug("post_a_b raw arguments from the Form fields:"+str(payload))

        for field in fields:

            #DATA

            self.logger.debug(field['name'])

            #Verify submitted field exists in the blueprint
            new_raw = ''
            if payload.get(field['name']):
                new_raw = payload.get(field['name'])
                self.logger.debug('Using: '+str(field['name'])+':'+str(new_raw))
            else:
                self.logger.debug('Inserting default value for field '+str(field['name'])+': '+str(field['default']))
                # Instead of skipping we put the field's default value
                new_raw = field['default']

            if field['type'] == 'object':
                try:
                    if isinstance(new_raw, dict):
                        item_values[field['name']] = new_raw
                    else:
                        item_values[field['name']] = json.loads(new_raw.strip())
                except:
                    if new_raw == '':
                        item_values[field['name']] = {}
                    else:
                        item_values[field['name']] = str(new_raw).strip()

            elif field['type'] == 'array':
                try:
                    if isinstance(new_raw, list):
                        item_values[field['name']] = new_raw
                    elif new_raw is None or (isinstance(new_raw, str) and not str(new_raw).strip()):
                        item_values[field['name']] = []
                    else:
                        item_values[field['name']] = json.loads(str(new_raw).strip())
                except Exception as e:
                    print(f'CPI > Blueprint : Type : array > Error details: {str(e)}')
                    print(f'CPI > Blueprint : Type : array > Error type: {type(e).__name__}')
                    if new_raw is None or (isinstance(new_raw, str) and not str(new_raw).strip()):
                        item_values[field['name']] = []
                    else:
                        item_values[field['name']] = str(new_raw).strip()

            elif field['type'] == 'timestamp':
                new_raw = new_raw.strip()
                try:
                    # Check if new_raw can be converted to a float first
                    float_value = float(new_raw)
                    # Convert to int and check if it's a valid timestamp
                    timestamp_value = int(float_value)

                    # Check if it's a valid timestamp (non-negative)
                    if timestamp_value >= 0:
                        # If the timestamp is in seconds, convert to milliseconds
                        if timestamp_value < 1000000000:  # Less than 1 billion means it's in seconds
                            timestamp_value *= 1000
                        item_values[field['name']] = timestamp_value  # Assign the valid timestamp
                    else:
                        item_values[field['name']] = None  # Handle negative timestamp
                except (ValueError, OverflowError):
                    # If not a valid float or int, try to parse it as a date
                    try:
                        date_value = datetime.strptime(new_raw, '%Y-%m-%d')  # Adjusted format for "YYYY-MM-DD"
                        item_values[field['name']] = int(date_value.timestamp() * 1000)  # Convert to milliseconds
                    except ValueError:
                        item_values[field['name']] = None  # Handle invalid date format

            elif field['type'] == 'string':
                if new_raw:
                    item_values[field['name']] = str(new_raw).strip()
                else:
                    item_values[field['name']] = ''

            else:
                if new_raw:
                    item_values[field['name']] = str(new_raw).strip()
                else:
                    item_values[field['name']] = None

            item_values[field['name']] = self.sanitize(item_values[field['name']])

        item = {}

        item['added'] = datetime.now().isoformat()
        item['modified'] = datetime.now().isoformat()
        item['license'] = 'CC BY'
        item['public'] = False
        item['blueprint'] = blueprint['uri']
        item['portfolio'] = portfolio
        item['org'] = org
        item['ring'] = ring
        item['blueprint_version'] = blueprint['version']

        if 'singleton' in blueprint and blueprint['singleton'] is True:
            item['_id'] = "00000000-0000-0000-0000-000000000000"
        else:
            item['_id'] = str(uuid.uuid4())

        item['attributes'] = item_values

        index_string = self.generate_index_string(blueprint,org,item_values)
        if index_string:
            item['path_index'] = index_string
        elif 'path_index' in item:
            del item['path_index']

        return item

    def construct_put_item(self,portfolio,org,ring,idx,payload):
        '''
        Creates an updated item based on an existing item following the blueprint .
        Notice that the payload contains only the fields that have been changed.
        This function completes the rest of the item based on the existing document.

        @NOTES:
          - "request.url" are the arguments that come via url
          - "request.form" are the arguments that come via form

        @IN:
          request.url = {(string):(string),}
          request.form = {(string):(string),}
          portfolio = (string)
          org = (string)
          ring = (string)
          idx = (string)

        @OUT:
          ok:(item_id)
          ko:False

        @COLLATERAL:
          - Update document in DB

        '''

        #1. Pull the document that we need to update
        updated_item = self.DAM.get_a_b_c(portfolio,org,ring,idx)
        #self.logger.debug('Item from DB:'+str(updated_item))

        #2. Pull the Blueprint listed in that document

        version = 'last'

        blueprint = self.BPC.get_blueprint('irma',ring,version)
        if not isinstance(blueprint, dict) or blueprint.get('success') is False or 'fields' not in blueprint:
            missing = (
                blueprint.get('message', 'unknown')
                if isinstance(blueprint, dict)
                else 'not a blueprint document'
            )
            raise ValueError(
                f"Blueprint for ring '{ring}' not found or invalid ({missing}). "
                f"Expected irn:blueprint:irma:{ring} with a 'fields' array."
            )
        fields = blueprint['fields']

        #3. Convert incoming request payload to JSON

        #self.logger.debug('CPI Payload:'+str(payload))
        #print(f"CPI TYPE:{type(payload).__name__}")
        #self.logger.debug(blueprint['fields'])

        #4. Check that the payload follows the Blueprint
        putNeeded = False

        for field in fields:
            self.logger.debug('>>:'+field['name'])
            # Use key membership, not truthiness: [] and 0 are valid updates (e.g. clear flights).
            if field['name'] in payload:
                self.logger.debug('Found:'+field['name'])
                # Attribute exists in the blueprint
                new_raw = payload[field['name']]

                if field['type'] == 'object':
                   #print(f'Field declared as object: {field["name"]}')

                    # Check if new_raw is already a dict
                    if isinstance(new_raw, dict):
                        updated_item['attributes'][field['name']] = new_raw
                        putNeeded = True
                    else:
                        try:
                            updated_item['attributes'][field['name']] = json.loads(new_raw.strip())
                            putNeeded = True
                        except:
                            updated_item['attributes'][field['name']] = str(new_raw).strip()
                            putNeeded = True

                elif field['type'] == 'array':

                    # Check if new_raw is already a list
                    if isinstance(new_raw, list):
                        updated_item['attributes'][field['name']] = new_raw
                        putNeeded = True
                    else:
                        try:
                            updated_item['attributes'][field['name']] = json.loads(new_raw.strip())
                            putNeeded = True
                        except json.JSONDecodeError as e:
                            # Try converting JavaScript syntax to JSON
                            try:
                                converted_json = convert_js_to_json(new_raw.strip())
                                updated_item['attributes'][field['name']] = json.loads(converted_json)
                                putNeeded = True
                            except Exception as conversion_error:
                                # Try the advanced converter
                                try:
                                    converted_json = convert_js_to_json_advanced(new_raw.strip())
                                    updated_item['attributes'][field['name']] = json.loads(converted_json)
                                    putNeeded = True
                                except Exception as advanced_error:
                                    # Try the robust converter
                                    try:
                                        converted_json = convert_js_to_json_robust(new_raw.strip())
                                        updated_item['attributes'][field['name']] = json.loads(converted_json)
                                        putNeeded = True
                                    except Exception as robust_error:
                                        # Try the simple converter
                                        try:
                                            converted_json = convert_js_to_json_simple(new_raw.strip())
                                            updated_item['attributes'][field['name']] = json.loads(converted_json)
                                            putNeeded = True
                                        except Exception as simple_error:
                                            updated_item['attributes'][field['name']] = str(new_raw).strip()
                                            putNeeded = True
                        except Exception as e:
                            updated_item['attributes'][field['name']] = str(new_raw).strip()
                            putNeeded = True

                else:

                    '''
                        Accept the value when it is non-empty, OR when the field is
                        optional (allowing the caller to clear a non-required field
                        with an empty string).

                        len>0  |  field['required']  |  accept
                        --------------------------------------
                        True   |   False             |  True
                        True   |   True              |  True
                        False  |   False             |  True   (clear optional field)
                        False  |   True              |  False  (required → reject)
                    '''

                    if len(str(new_raw)) > 0 or not field['required']:

                        self.logger.debug('Field OK:'+field['name'])
                        #Attribute complies with "Required" prerequisite

                        #5.Update attributes based on what has been sent in the request
                        updated_item['attributes'][field['name']] = new_raw
                        putNeeded = True

                        #break

                    else:
                        self.logger.debug('Attribute is required:'+field['name'])
                        return {'error':'Attribute is required'}

        if not putNeeded:
            return {'error':'Attributes not recognized'}

        # DEPRECATED (Update the index string.)
        # YOU CAN'T UPDATE THE LSI
        '''
        index_string = self.generate_index_string(blueprint, updated_item['attributes'] )
        self.logger.debug('(GIS) > Index string:'+str(index_string))
        if index_string:
            updated_item['path_index'] = index_string
        elif 'path_index' in updated_item:
            del updated_item['path_index']
        '''

        #6. Return to save document to DB
        #updated_item['modified'] = datetime.now().isoformat()
        #print('CPI > OUTPUT:')
        #print(updated_item)

        updated_item = self.sanitize(updated_item)

        return updated_item

    #DEPRECATED
    def get_a_index(self,portfolio,prefix_path):

        items = []

        result = self.DAM.get_a_index(portfolio,prefix_path)
        self.logger.debug('get_a_index results:' + json.dumps(result))  # Convert result to string

        if 'error' in result:
            self.logger.error(result['error'])

            result['success'] = False
            result['message'] = 'Items could not be retrieved (@get_a_index)'
            result['error'] = result['error']
            status = 400
            return result

        i=0
        for row in result['items']:

            i += 1
            '''
            i += 1
            if lastkey and i==1:
                #If lastkey was sent, ignore first item
                #as it was the last item in the last page
                continue
            '''

            item = {}
            item = row['attributes']
            item['_id'] = row['_id']

            if 'modified' in row:
                item['_modified'] = row['modified']
            else:
                item['_modified'] = ''

            if 'path_index' in row:
                item['_index'] = row['path_index']
            else:
                item['_index'] = ''

            if item:
                items.append(item)

        '''
        if len(items)>1 and sort:

            self.sort = sort
            items = sorted(items, key=self.sort_item_list, reverse=sort_reverse)
        '''
        self.logger.debug('NUMBER OF ITEMS:'+str(i))

        #return items,result['lastkey']
        return items

    #DEPRECATED
    def get_a_b_index(self,portfolio,prefix_path):

        items = []

        result = self.DAM.get_a_b_index(portfolio,prefix_path)
        self.logger.debug('get_a_b_index results:' + json.dumps(result))  # Convert result to string

        if 'error' in result:
            self.logger.error(result['error'])

            result['success'] = False
            result['message'] = 'Items could not be retrieved (@get_a_b_index)'
            result['error'] = result['error']
            status = 400
            return result

        i=0
        for row in result['items']:

            i += 1
            '''
            i += 1
            if lastkey and i==1:
                #If lastkey was sent, ignore first item
                #as it was the last item in the last page
                continue
            '''

            item = {}
            item = row['attributes']
            item['_id'] = row['_id']

            if 'modified' in row:
                item['_modified'] = row['modified']
            else:
                item['_modified'] = ''

            if 'path_index' in row:
                item['_index'] = row['path_index']
            else:
                item['_index'] = ''

            if item:
                items.append(item)

        '''
        if len(items)>1 and sort:

            self.sort = sort
            items = sorted(items, key=self.sort_item_list, reverse=sort_reverse)
        '''
        self.logger.debug('NUMBER OF ITEMS:'+str(i))

        #return items,result['lastkey']
        return items

    def get_a_b_query(self,query):

        '''
        Incoming query object must have the following shape
            {
            'portfolio':<portfolio_id>,
            'org':<org_id>,
            'ring':<ring_id>,
            'operator':<begins_with|chrono|greater_than|less_than|equal_to>,
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

        #prefix = f'irn:h_index:{org}:{ring}:{index_tail}'

        if 'operator' not in query or not query['operator']:
            return {'success':False,'message':'No query'}

        operator = query['operator']
        #portfolio_index = f'irn:data:{query["portfolio"]}'

        # SWITCH
        # The index begins with ...
        if operator=='begins_with':

            response = self.DAM.get_a_b_beginswith(query)

        # The index is a timestamp, return results in chronological order
        if operator=='chrono':

            response = self.DAM.get_a_b_beginswith(query)

        # The index is numeric, return anything greater than ...
        if operator=='greater_than':

            response = self.DAM.get_a_b_greaterthan(query)

        # The index is numeric, return anything less than ...
        if operator=='less_than':

            response = self.DAM.get_a_b_lessthan(query)

        # The index is equal to ...
        if operator=='equal_to':

            response = self.DAM.get_a_b_equalto(query)

        items = []
        #response = self.DAM.get_a_b(portfolio,org,ring,limit=limit,lastkey=lastkey)
        result = {}
        if 'error' in response:
            self.logger.error(response['error'])

            result['success'] = False
            result['message'] = 'Items could not be retrieved(@get_a_b_query)'
            result['error'] = response['error']
            status = 400
            return result

        i=0
        for row in response['items']:

            i += 1

            if query['lastkey'] and i==1:
                #If lastkey was sent, ignore first item
                #as it was the last item in the last page
                continue

            item = {}
            item = row['attributes']
            item['_id'] = row['_id']

            if 'modified' in row:
                item['_modified'] = row['modified']
            else:
                item['_modified'] = ''

            if 'path_index' in row:
                item['_index'] = row['path_index']
            else:
                item['_index'] = ''

            if item:
                items.append(item)

        last_id = response['lastkey']

        result['success'] = True
        result['items'] = items
        result['last_id'] = last_id

        self.logger.debug('NUMBER OF ITEMS (QUERY):'+str(i))

        return result

    def get_a_b(self,portfolio,org,ring,limit=1000,lastkey=None,sort=None):
        '''
        Get page of items

        @NOTES:
          - The "human" parameter determines whether ids or labels are returned as keys

        @IN:
          portfolio = (string)
          org = (string)
          ring = (string)
          limit = (integer)
          lastkey = (string)
          endkey = (string)
          sort = (string)

        @OUT:
          [{(item)}]

        '''

        items = []

        response = self.DAM.get_a_b(portfolio,org,ring,limit=limit,lastkey=lastkey)

        #self.logger.debug(f'RRR2: {result}')

        result = {}
        if 'error' in response:
            self.logger.error(response['error'])

            result['success'] = False
            result['message'] = 'Items could not be retrieved (@get_a_b)'
            result['error'] = response['error']
            status = 400
            return result

        i=0
        for row in response['items']:

            i += 1

            if lastkey and i==1:
                #If lastkey was sent, ignore first item
                #as it was the last item in the last page
                continue

            item = {}
            item = row['attributes']
            item['_id'] = row['_id']

            if 'modified' in row:
                item['_modified'] = row['modified']
            else:
                item['_modified'] = ''

            if 'path_index' in row:
                item['_index'] = row['path_index']
            else:
                item['_index'] = ''

            if item:
                items.append(item)

        '''result = {
                'items': items,
                'lastkey': endkey  # This will be passed as 'lastkey' in the next call if needed
            }'''

        last_id = response['last_id']

        if len(items)>1 and sort:
            items = sorted(items, key=lambda item: item[sort], reverse=True)

        result['success'] = True
        result['items'] = items
        result['last_id'] = last_id

        self.logger.debug('NUMBER OF ITEMS:'+str(i))

        return result

    def post_a_b(self,portfolio,org,ring,payload):
        '''
        Creates new item
        '''

        try:
            item = self.construct_post_item(portfolio,org,ring,payload)

            self.logger.debug('Prepared Item:'+str(item))

            response = self.DAM.post_a_b(portfolio,org,ring,item)

            result = {}
            status = 0

            if 'error' not in response:
                result['success'] = True
                result['message'] = 'Item saved (POST)'
                result['path'] = str(portfolio+'/'+org+'/'+ring+'/'+item['_id'])
                result['item'] = item
                status = 200
                self.search_index.index_document(portfolio, org, ring, item)

            else:
                result['success'] = False
                result['message'] = 'Item could not be saved'
                result['error'] = response['error']
                status = 400

            self.logger.debug('Returned object:'+str(result))

            return result, status

        except Exception as e:
            self.logger.error(f'Error in post_a_b: {str(e)}')
            result = {
                'success': False,
                'message': 'Item could not be saved due to an exception',
                'error': str(e)
            }
            status = 500
            return result, status

    def get_a_b_c(self,portfolio,org,ring,idx):
        '''
        Gets an existing item
        '''
        self.logger.debug('IDX:'+str(idx))

        response = self.DAM.get_a_b_c(portfolio,org,ring,idx)

        result = {}

        if 'error' in response:
            result['success'] = False
            result['message'] = 'Item could not be retrieved'
            result['error'] = response['error']
            status = 400

        elif 'attributes' not in response:
            result['success'] = False
            result['message'] = 'Item could not be retrieved, No Attributes'
            result['error'] = response['error']
            status = 400

        else:
            result = response['attributes']
            result['_id'] = response['_id']

            if 'modified' in response:
                result['_modified'] = response['modified']
            else:
                result['_modified'] = ''

            if 'path_index' in response:
                result['_index'] = response['path_index']
            else:
                result['_index'] = ''

            if 'modified' in response:
                result['_modified'] = response['modified']
            else:
                result['_modified'] = ''

        self.logger.debug('Returned object:'+str(result))

        return result

    def put_a_b_c(self,portfolio,org,ring,idx,payload):
        '''
        Partial updates to an existing document.
        FE only needs to send the field to be updated. No need to send the entire document.
        '''
        #1.

        result = {}

        #self.logger.debug('Icoming put object:'+str(payload))
        item = self.construct_put_item(portfolio,org,ring,idx,payload)

        if 'error' in item:
            self.logger.debug(str(item))
            result['success'] = False
            result['message'] = 'Item could not be saved'
            result['error'] = item['error']
            status = 400
            return result, status

        self.logger.debug('Updating Item:'+str(item))
        response = self.DAM.put_a_b_c(portfolio,org,ring,idx,item)

        #self.logger.debug('Update response:'+str(response))

        if 'error' not in response:
            result['success'] = True
            result['message'] = 'Item saved (PUT)'
            result['path'] = str(portfolio+'/'+org+'/'+ring+'/'+idx)
            print(f"[DOC] save | path={result['path']} | status=success")
            status = 200
            self.logger.debug('Returned object:'+str(result))
            self.search_index.index_document(portfolio, org, ring, item)

            return result, status

        else:
            result['success'] = False
            result['message'] = 'Item could not be saved'
            result['error'] = response['error']
            print(f"[DOC] save | path={portfolio+'/'+org+'/'+ring+'/'+idx} | status=failed") #logging error
            status = 500
            self.logger.debug('Returned object:'+str(result))

            return result,status



    def delete_a_b_c(self,portfolio,org,ring,idx):
        '''
        Delete an existing document.
        '''

        self.logger.debug('Item to delete:'+str(idx))

        response = self.DAM.delete_a_b_c(portfolio,org,ring,idx)

        result = {}

        if 'error' not in response:
            result['success'] = True
            result['message'] = 'Item deleted'
            result['path'] = str(portfolio+'/'+org+'/'+ring+'/'+idx)
            status = 200
            self.logger.debug('Returned object:'+str(result))
            self.search_index.delete_document(portfolio, org, ring, idx)

            return result, status

        else:
            result['success'] = False
            result['message'] = 'Item could not be deleted'
            result['error'] = response['error']
            status = 500
            self.logger.debug('Returned object:'+str(result))

            return result,status

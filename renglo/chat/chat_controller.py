# chat_controller.py
import copy
import json
import logging
import traceback
import uuid
import boto3
from decimal import Decimal

from flask import current_app
from flask_cognito import current_cognito_jwt
from datetime import datetime
from renglo.docs.docs_controller import DocsController
from renglo.chat.chat_model import ChatModel
from ..common import *

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
        # Prefix query: legacy rows used entity_index == entity_id; new rows use
        # entity_index == "{entity_id}/{thread_id}" so multiple threads can coexist.
        secondary = f"{entity_id}"

        # Page size per DynamoDB Query request; query_chat paginates until all keys are read.
        limit = 1000
        sort = 'desc'

        response = self.CHM.query_chat(index, secondary, limit, sort=sort)
        if response.get('success') and response.get('items'):
            items = response['items']

            def _thread_time(it):
                try:
                    return float(it.get('time') or 0)
                except (TypeError, ValueError):
                    return 0.0

            response['items'] = sorted(items, key=_thread_time, reverse=True)

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
        thread_id = str(uuid.uuid4())
        # Unique sort key per thread so list_threads (begins_with entity_id) returns all threads.
        secondary = f"{entity_id}/{thread_id}"

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
            '_id': thread_id,
        }

        response = self.CHM.create_chat(data)

        return response

    def move_thread(self, portfolio, org, entity_type, old_entity_id, new_entity_id, thread_id):
        """
        Re-home a chat thread (thread row + its turns + workspaces) from
        ``old_entity_id`` to ``new_entity_id`` — e.g. the dashboard
        ``{org}-travel-chat`` conversation that birthed a trip is moved to
        ``{org}-{tripId}`` so the trip's own chat shows the history instead of
        an empty thread.

        Non-destructive: every row is copied under the new entity sort key and
        the *source* thread is merely deactivated (so the old entity starts a
        fresh conversation). The original turn/workspace rows are left in place
        as harmless, unreferenced cruft — no delete, no risk of half-migration.

        Idempotent: if the thread already exists under ``new_entity_id`` it is a
        no-op, so retries / double-fires are safe.

        Only ``entity_index`` (the DynamoDB sort key) carries the entity id, so
        re-keying is a prefix rewrite; ``index`` depends on entity_type, which
        does not change.
        """
        try:
            if not all([portfolio, org, entity_type, old_entity_id, new_entity_id, thread_id]):
                return {"success": False, "message": "Missing required parameters"}
            if old_entity_id == new_entity_id:
                return {"success": True, "message": "Same entity, nothing to move", "moved": 0}

            # Idempotency: skip if the thread is already homed under the new entity.
            existing = self.list_threads(portfolio, org, entity_type, new_entity_id)
            for t in (existing.get('items') or []):
                if t.get('_id') == thread_id:
                    return {"success": True, "message": "Already migrated", "moved": 0}

            old_prefix = f"{old_entity_id}/{thread_id}"
            new_prefix = f"{new_entity_id}/{thread_id}"

            thread_index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread:*/*"
            turn_index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"
            workspace_index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/workspace:*/*/*/*"

            moved = 0

            # Turns + workspaces: copy each row under the new sort key.
            for idx in (turn_index, workspace_index):
                resp = self.CHM.query_chat(idx, old_prefix, limit=1000, sort='asc')
                for row in (resp.get('items') or []):
                    new_row = copy.deepcopy(dict(row))
                    new_row['entity_index'] = str(row.get('entity_index', '')).replace(
                        old_prefix, new_prefix, 1
                    )
                    ctx = new_row.get('context')
                    if isinstance(ctx, dict) and ctx.get('entity_id') == old_entity_id:
                        ctx['entity_id'] = new_entity_id
                    self.CHM.create_chat(new_row)
                    moved += 1

            # Thread row: copy under the new entity, then deactivate the source.
            thread_resp = self.CHM.query_chat(thread_index, old_prefix, limit=1000, sort='asc')
            for row in (thread_resp.get('items') or []):
                if row.get('_id') != thread_id:
                    continue
                new_row = copy.deepcopy(dict(row))
                new_row['entity_index'] = str(row.get('entity_index', '')).replace(
                    old_prefix, new_prefix, 1
                )
                new_row['entity_id'] = new_entity_id
                new_row['is_active'] = True
                self.CHM.create_chat(new_row)
                moved += 1

                old_row = copy.deepcopy(dict(row))
                old_row['is_active'] = False
                self.CHM.update_chat(old_row)

            return {
                "success": True,
                "moved": moved,
                "thread_id": thread_id,
                "from": old_entity_id,
                "to": new_entity_id,
            }
        except Exception as e:
            current_app.logger.error(f"Error in move_thread: {str(e)}")
            return {"success": False, "message": f"Error moving thread: {str(e)}", "status": 500}

    # TURNS
    # There is a document per turn in the database
    # Every turn document contains a list of messages that belong to that turn

    @staticmethod
    def _event_type_name(ev):
        if not isinstance(ev, dict):
            return None
        return ev.get("type") or ev.get("_type")

    @staticmethod
    def _tmp_key_five_tuple(key):
        """S3 tmp key: portfolio / org / entity (e.g. noma) / YYYY-MM-DD / object_id (5 path segments)."""
        if not key or not isinstance(key, str):
            return None
        parts = [p for p in key.strip().strip("/").split("/") if p]
        if len(parts) < 5:
            return None
        return tuple(parts[:5])

    def _tmp_get_json(self, dcc: DocsController, key: str):
        t = self._tmp_key_five_tuple(key)
        if not t:
            return None
        portfolio, org, entity, date_str, object_id = t
        r = dcc.tmp_get(portfolio, org, entity, date_str, object_id)
        if not r or not r.get("success") or "content" not in r:
            return None
        body = r["content"]
        try:
            if hasattr(body, "get_data"):
                raw = body.get_data()
                if isinstance(raw, (bytes, bytearray)):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    text = str(raw)
            else:
                return None
            return json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            current_app.logger.warning("tmp_get JSON parse failed for key %s: %s", key, e)
            return None

    def _first_tmp_artifact_from_tool_result(self, event):
        if self._event_type_name(event) != "tool_result":
            return None
        out = event.get("out") or event.get("_out")
        if not isinstance(out, dict):
            return None
        content = out.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        if not isinstance(content, dict):
            return None
        res = content.get("result")
        if res is None:
            return None
        rows = res if isinstance(res, list) else [res]
        for row in rows:
            if not isinstance(row, dict):
                continue
            iface = str(row.get("interface") or row.get("_interface") or "flights")
            nxt = row.get("_next")
            r_out = row.get("out") or row.get("_out")
            if not isinstance(r_out, dict):
                continue
            tool_call_id = r_out.get("tool_call_id") or ""
            inner = r_out.get("content")
            if inner is None:
                continue
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
            parts = inner if isinstance(inner, list) else [inner]
            for part in parts:
                if not isinstance(part, dict):
                    continue
                art = part.get("artifact")
                if not isinstance(art, dict):
                    continue
                if art.get("type") is not None and art.get("type") != "tmp_artifact":
                    continue
                tkey = art.get("key")
                if tkey:
                    return (iface, nxt, tool_call_id, tkey)
        return None

    def _first_tmp_artifact_from_top_level_tool_rs(self, event):
        """Top-level ``tool_rs`` with tmp_artifact in ``_out.content`` (not under tool_result)."""
        if self._event_type_name(event) != "tool_rs":
            return None
        iface = str(event.get("interface") or event.get("_interface") or "flights")
        nxt = event.get("_next")
        out = event.get("out") or event.get("_out")
        if not isinstance(out, dict):
            return None
        tool_call_id = out.get("tool_call_id") or ""
        content = out.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        if content is None:
            return None
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            if not isinstance(part, dict):
                continue
            art = part.get("artifact")
            if not isinstance(art, dict):
                continue
            if art.get("type") is not None and art.get("type") != "tmp_artifact":
                continue
            tkey = art.get("key")
            if tkey:
                return (iface, nxt, tool_call_id, tkey)
        return None

    @staticmethod
    def _new_tool_rs_event(
        interface: str,
        next_ptr,
        tool_call_id,
        document,
    ) -> dict:
        if isinstance(document, dict):
            content = [document]
        elif isinstance(document, list):
            content = document
        else:
            content = [document]
        out_block = {
            "content": content,
            "role": "tool",
            "tool_call_id": str(tool_call_id) if tool_call_id is not None else "",
        }
        new_event: dict = {
            "type": "tool_rs",
            "_type": "tool_rs",
            "interface": interface,
            "_interface": interface,
            "out": out_block,
            "_out": out_block,
        }
        if next_ptr is not None and next_ptr != "":
            new_event["_next"] = next_ptr
        return new_event

    def _replace_with_resolved_if_tmp_artifact(self, event, dcc: DocsController):
        spec = self._first_tmp_artifact_from_tool_result(event)
        if not spec:
            spec = self._first_tmp_artifact_from_top_level_tool_rs(event)
        if not spec:
            return None
        interface, nxt, tool_call_id, akey = spec
        document = self._tmp_get_json(dcc, akey)
        if document is None:
            return None
        return self._new_tool_rs_event(interface, nxt, tool_call_id, document)

    def _event_list_for_last_turn(self, last: dict):
        if isinstance(last.get("events"), list):
            return last["events"]
        if isinstance(last.get("messages"), list):
            return last["messages"]
        return []

    def _resolve_last_turn_tmp_artifacts(self, response: dict) -> None:
        items = response.get("items")
        if not items or not isinstance(items, list):
            return
        last = items[-1]
        if not isinstance(last, dict):
            return
        evl = self._event_list_for_last_turn(last)
        if not evl or not isinstance(evl, list):
            return
        dcc = DocsController(config=self.config)
        for i, event in enumerate(evl):
            if not isinstance(event, dict):
                continue
            t = self._event_type_name(event)
            if t not in ("tool_result", "tool_rs"):
                continue
            replacement = self._replace_with_resolved_if_tmp_artifact(event, dcc)
            if replacement is not None:
                evl[i] = replacement

    def list_turns(
        self, portfolio, org, entity_type, entity_id, thread_id, resolve=False
    ):
        """List turns. ``resolve`` is only for HTTP message APIs that inline tmp
        documents into ``tool_rs`` for the client. Agents, triage, and any code
        that needs the stored chain of thought (raw ``tmp_artifact`` pointers)
        must call with ``resolve=False`` (the default)."""
        index = f"irn:chat:{portfolio}:{org}:{entity_type}/thread/time/turn:*/*/*/*"

        query = f"{entity_id}/{thread_id}"

        limit = 50
        sort = 'asc'

        response = self.CHM.query_chat(index, query, limit, sort=sort)

        if not resolve or not response.get("success"):
            return response

        try:
            out = copy.deepcopy(response)
            self._resolve_last_turn_tmp_artifacts(out)
            return out
        except Exception as e:
            current_app.logger.error("list_turns resolve tmp_artifacts: %s", e, exc_info=True)
            traceback.print_exc()
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
                'documents': {},
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

            if 'documents' in payload and isinstance(payload['documents'], dict):
                if 'documents' not in item or not isinstance(item.get('documents'), dict):
                    item['documents'] = {}
                for k, v in payload['documents'].items():
                    item['documents'][k] = v
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

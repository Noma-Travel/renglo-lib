"""
WebSocket client wrapper that supports both AWS API Gateway and local dev WebSocket service.
"""
import json
import boto3
import requests
from decimal import Decimal
from typing import Any, Dict


def _outbound_local_http_url(websocket_url: str) -> str:
    """ws(s) → http(s), trim, map bind-only hosts (0.0.0.0, [::]) to localhost,
    and target the local dev service's HTTP push endpoint.

    The local WebSocket dev service (extensions/wss/dev_ws_service.py) reserves
    the root ``/`` and ``/ws`` for the browser's WebSocket upgrade and exposes
    the server→client push as ``POST /send_to_client``. WEBSOCKET_CONNECTIONS is
    shared with the frontend (it needs ``/ws``), so the correct backend push
    URL is derived here instead of in config: strip the ws path down to the
    host, then append ``/send_to_client``. Posting to the bare host root hits a
    WebSocket route and 405s.
    """
    u = (websocket_url or "").strip()
    if u.startswith("ws://"):
        u = "http://" + u[5:]
    elif u.startswith("wss://"):
        u = "https://" + u[6:]
    elif not u.startswith(("http://", "https://")):
        u = "http://" + u
    u = u.rstrip("/").replace("/ws", "")
    # Copy-paste from server logs; Windows cannot use 0.0.0.0 / [::] as HTTP client target.
    u = u.replace("://0.0.0.0", "://localhost", 1)
    u = u.replace("://[::]", "://localhost", 1)
    return u.rstrip("/") + "/send_to_client"


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)


class WebSocketClient:
    """
    WebSocket client that abstracts away the differences between:
    - AWS API Gateway WebSocket Management API (production)
    - Local dev WebSocket service (development)

    Local URLs are normalized for outbound HTTP the same way on Linux, macOS, and Windows
    (bind-only hosts like 0.0.0.0 / :: become localhost).
    """
    
    def __init__(self, websocket_url: str):
        """
        Initialize WebSocket client.
        
        Args:
            websocket_url: WebSocket or HTTP URL. If it looks like local dev (localhost,
                127.0.0.1, 0.0.0.0, ::, ws://…), POST is used against the dev bridge; otherwise
                AWS API Gateway management API is used.
        """
        self.websocket_url = websocket_url
        self.is_local = False
        self.local_ws_url = None
        self.apigw_client = None
        
        if not websocket_url:
            return
        
        # Local dev: explicit loopback, any-interface bind, or ws(s) scheme
        w = websocket_url.strip().lower()
        looks_local = (
            "localhost" in w
            or "127.0.0.1" in w
            or "0.0.0.0" in w
            or "[::]" in w
            or "[::1]" in w
            or "://::" in w
            or w.startswith("ws://")
            or w.startswith("wss://")
        )
        if looks_local:
            print('Initializing local Websocket service')
            self.is_local = True
            self.local_ws_url = _outbound_local_http_url(websocket_url)
        else:
            # AWS API Gateway
            self.is_local = False
            try:
                self.apigw_client = boto3.client("apigatewaymanagementapi", endpoint_url=websocket_url)
            except Exception as e:
                print(f"Error initializing AWS API Gateway client: {e}")
                self.apigw_client = None
    
    def is_configured(self) -> bool:
        """Check if WebSocket client is properly configured."""
        if self.is_local:
            return self.local_ws_url is not None
        else:
            return self.apigw_client is not None
    
    def send_message(self, connection_id: str, payload: Dict[str, Any]) -> bool:
        """
        Send a message to a WebSocket connection.
        
        Args:
            connection_id: The WebSocket connection ID
            payload: The message payload to send
            
        Returns:
            bool: True if message was sent successfully, False otherwise
        """
        if not connection_id or not self.is_configured():
            return False
        
        try:
            if self.is_local:
                # Local dev WebSocket service - use HTTP POST
                #print(f"Posting locally to: {self.local_ws_url}")
                response = requests.post(
                    f"{self.local_ws_url}",
                    json={
                        "connection_id": connection_id,
                        "payload": payload
                    },
                    timeout=5
                )
                response.raise_for_status()
                result = response.json()
                if not result.get('ok', False):
                    print(f'Error sending message to local WebSocket: {result.get("error", "Unknown error")}')
                    return False
            else:
                # AWS API Gateway
                print(f"Posting to AWS WSS")
                self.apigw_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps(payload, cls=DecimalEncoder)
                )
            
            return True
            
        except requests.exceptions.RequestException as e:
            print(f'Error sending message to local WebSocket: {str(e)}')
            return False
        except Exception as e:
            # Handle AWS API Gateway exceptions
            if not self.is_local and self.apigw_client:
                # Check if it's a GoneException (connection closed)
                exception_name = type(e).__name__
                if exception_name == 'GoneException':
                    print(f'Connection is no longer available')
                    return False
            print(f'Error sending message: {str(e)}')
            return False


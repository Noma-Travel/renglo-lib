import re
import hashlib
import os
import sys
import importlib.util


def get_username_from_email(email):
    # Extract the part before the @
    username = email.split('@')[0]
    # Remove any non-alphanumeric characters
    cleaned_username = re.sub(r'[^a-zA-Z0-9]', '', username)
    return cleaned_username


def create_md5_hash(input_string, num_digits):
    # Create an MD5 hash object
    md5_hash = hashlib.md5()
    # Update the hash object with the input string encoded as bytes
    md5_hash.update(input_string.encode('utf-8'))
    # Get the full hexadecimal MD5 hash
    full_hash = md5_hash.hexdigest()
    # Return the first N digits of the hash
    return full_hash[:num_digits]


def load_config():
    """
    Load configuration for handlers from env_config.py or environment variables.

    Handlers are independent of Flask and need their own way to access config.
    This function is used by handlers in all extensions
    to load the system configuration before initializing controllers.

    Loading Strategy:
    1. Try to load from system/env_config.py (local development)
    2. Fall back to environment variables (Lambda/production)
    3. Merge both sources (env vars take precedence)

    Returns:
        dict: Configuration dictionary with all uppercase config variables

    Usage in handlers:
        from renglo.common import load_config

        class MyHandler:
            def __init__(self):
                config = load_config()
                self.DAC = DataController(config=config)
                self.AUC = AuthController(config=config)
    """
    config = {}

    # Try multiple paths to find env_config.py
    possible_paths = []

    # 1. Try relative to current working directory
    possible_paths.append(os.path.join(os.getcwd(), 'system', 'env_config.py'))

    # 2. Try to find workspace root by looking for marker directories
    current_dir = os.getcwd()
    while current_dir != os.path.dirname(current_dir):  # Stop at filesystem root
        if os.path.exists(os.path.join(current_dir, 'system', 'env_config.py')):
            possible_paths.append(os.path.join(current_dir, 'system', 'env_config.py'))
            break
        # Look for workspace markers
        if any(os.path.exists(os.path.join(current_dir, marker))
               for marker in ['dev', 'extensions', 'console', 'system']):
            possible_paths.append(os.path.join(current_dir, 'system', 'env_config.py'))
            break
        current_dir = os.path.dirname(current_dir)

    # 3. Try relative from this module's location (renglo/common.py)
    # Go up: renglo -> renglo-lib -> dev -> root
    renglo_lib_path = os.path.dirname(os.path.dirname(__file__))
    workspace_root = os.path.dirname(os.path.dirname(renglo_lib_path))
    possible_paths.append(os.path.join(workspace_root, 'system', 'env_config.py'))

    env_config = None
    loaded_from = None

    # Try to load from each path
    for config_path in possible_paths:
        if os.path.exists(config_path):
            try:
                spec = importlib.util.spec_from_file_location("env_config", config_path)
                env_config = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(env_config)
                loaded_from = config_path
                break
            except Exception as e:
                print(f"Warning: Failed to load config from {config_path}: {e}", file=sys.stderr)
                continue

    if env_config:
        # Extract all uppercase variables (convention for config constants)
        for key in dir(env_config):
            if key.isupper() and not key.startswith('_'):
                config[key] = getattr(env_config, key)
        # Inject observability vars into os.environ so logger.py and debug_json.py can read them
        for obs_key in ('LOG_LEVEL', 'DEBUG_JSON', 'DEBUG_DIR'):
            if hasattr(env_config, obs_key):
                os.environ.setdefault(obs_key, str(getattr(env_config, obs_key)))
    else:
        print("Config file not found, using environment variables", file=sys.stderr)

    # Load from environment variables (overwrites file-based config)
    # This allows Lambda/production to use environment variables
    env_var_keys = [
        'WL_NAME', 'BASE_URL', 'FE_BASE_URL', 'APP_FE_BASE_URL', 'DOC_BASE_URL',
        'AWS_REGION', 'API_GATEWAY_ARN', 'ROLE_ARN', 'SYS_ENV',
        'DYNAMODB_ENTITY_TABLE', 'DYNAMODB_BLUEPRINT_TABLE', 'DYNAMODB_RINGDATA_TABLE',
        'DYNAMODB_REL_TABLE', 'DYNAMODB_CHAT_TABLE', 'DYNAMODB_SESSION_TABLE',
        'CSRF_SESSION_KEY', 'SECRET_KEY',
        'COGNITO_REGION', 'COGNITO_USERPOOL_ID', 'COGNITO_APP_CLIENT_ID',
        'COGNITO_CHECK_TOKEN_EXPIRATION',
        'PREVIEW_LAYER', 'S3_BUCKET_NAME',
        'OPENAI_API_KEY', 'WEBSOCKET_CONNECTIONS', 'AVIATION_STACK_API_KEY', 'RESEND_API_KEY',
        'ALLOW_DEV_ORIGINS', 'EXTERNAL_HANDLERS',
        'OPENSEARCH_ENDPOINT', 'OPENSEARCH_INDEX', 'OPENSEARCH_REFRESH',
        # Microsoft Outlook OAuth / token encryption
        'NEXT_PUBLIC_MS_TENANT_ID', 'NEXT_PUBLIC_MS_CLIENT_ID',
        'NEXT_PUBLIC_MS_CLIENT_SECRET', 'NEXT_PUBLIC_MS_REDIRECT_URI',
        'TOKENS_KMS_KEY_ID',
        # Invite / transactional email (auth_model.send_email, invite_user_funnel, invite_attendant)
        'INVITE_FROM', 'SES_INVITE_SENDER',
    ]

    env_loaded_count = 0
    for key in env_var_keys:
        if key in os.environ:
            config[key] = os.environ[key]
            env_loaded_count += 1

    if env_loaded_count > 0:
        print(f"Loaded {env_loaded_count} config values from environment variables")

    # Validate critical config exists
    critical_keys = ['DYNAMODB_RINGDATA_TABLE', 'DYNAMODB_ENTITY_TABLE']
    missing_keys = [key for key in critical_keys if key not in config]

    if missing_keys:
        raise RuntimeError(
            f"Critical configuration missing: {', '.join(missing_keys)}\n"
            f"Please set these as environment variables or in system/env_config.py"
        )

    return config

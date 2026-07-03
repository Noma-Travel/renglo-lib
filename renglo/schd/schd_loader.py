from renglo.logger import get_logger
import importlib
import logging
import os
import sys
import gc
import traceback

_logger_schd = logging.getLogger("agent.schd")


def _unload_handlers_enabled():
    """
    Whether handler modules should be removed from sys.modules (+ gc.collect())
    after each run.

    Controlled by SCHD_UNLOAD_HANDLERS ("1"/"true"/"on" to enable). When unset,
    defaults to ON only inside AWS Lambda (memory-constrained, single-request
    containers) and OFF everywhere else: in a long-running server, unloading on
    every call forces a full module reimport per request and the gc.collect()
    pause blocks ALL concurrent requests via the GIL.
    """
    raw = os.environ.get('SCHD_UNLOAD_HANDLERS')
    if raw is not None:
        return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(os.environ.get('AWS_LAMBDA_FUNCTION_NAME'))


class SchdLoader:

    def __init__(self, module_path="handlers"):
        self.module_path = module_path
        self.unload_handlers = _unload_handlers_enabled()



    def convert_module_name_to_class(self,input_string):
        # Step 1: Get the basename (last part of the path) in a cross-platform way
        after_slash = os.path.basename(input_string)

        # Step 2: Replace '_' with spaces
        words = after_slash.replace('_', ' ')

        # Step 3: Capitalize the first letter of each word
        capitalized_words = words.title()

        # Step 4: Remove spaces
        result = capitalized_words.replace(' ', '')

        return result



    def load_code_class(self, module_path, module_name, class_name, *args, **kwargs):
        """
        Dynamically loads a class from an installed Python package and returns an instance.

        This function is completely agnostic - it simply tries to import the module using importlib.
        If the package is installed (via pip), it will be found and imported.

        Args:
            module_path: Package name (e.g., "enerclave")
            module_name: Handler name (e.g., "geocoding_handler")
            class_name: Class name (e.g., "GeocodingHandler")
            *args, **kwargs: Arguments to pass to the handler constructor

        Returns:
            Handler instance or None if loading fails

        Example:
            # For a package installed via: pip install -e enerclave-module/
            # This will import: from enerclave.handlers.geocoding_handler import GeocodingHandler
            instance = load_code_class('enerclave', 'geocoding_handler', 'GeocodingHandler')
        """
        try:
            # Construct the full module path
            # Example: "enerclave.handlers.geocoding_handler"
            full_module_path = f"{module_path}.handlers.{module_name}"

            _logger_schd.debug("loading_module | %s", full_module_path)

            # Dynamically import the module
            # This will work if the package is installed (e.g., via pip install)
            module = importlib.import_module(full_module_path)

            # Get the class from the module
            class_ = getattr(module, class_name)

            _logger_schd.debug("class_loaded | %s", class_.__name__)

            # Instantiate the class
            # Check if it needs config (convention: handlers ending in 'onboardings' need config)
            if 'onboarding' in module_name.lower():
                # Pass config to handlers that need it
                # Try to get config from Flask if available, otherwise use empty dict
                config = {}
                try:
                    from flask import has_request_context, current_app
                    if has_request_context() and hasattr(current_app, 'renglo_config'):
                        config = current_app.renglo_config
                except (ImportError, RuntimeError):
                    pass
                instance = class_()
            else:
                # Most handlers don't need config in __init__
                instance = class_()

            _logger_schd.debug("instance_created | %s", instance.__class__.__name__)

            return instance

        except ModuleNotFoundError as e:
            # Module not found - package probably not installed
            _logger_schd.error("module_not_found | %s | %s", full_module_path, e)
            _logger_schd.error("install_hint | pip install -e %s-module/", module_path)
            return None

        except AttributeError as e:
            # Class not found in module
            _logger_schd.error("class_not_found | class=%s | module=%s | %s", class_name, full_module_path, e)
            return None

        except TypeError as e:
            # Error instantiating the class
            _logger_schd.error("instantiation_error | class=%s | %s", class_name, e)
            return None

        except Exception as e:
            # Any other error
            _logger_schd.error("load_class_failed | class=%s | module=%s | %s", class_name, full_module_path, e)
            import traceback
            _logger_schd.error(traceback.format_exc())
            return None



    def load_and_run(self, module_name, *args, **kwargs):
        """
        Loads a module, runs its class method, then unloads it.

        Supports two formats:
        - Two parts: module_path.module_name (e.g., "arbitium.helper_rds")
        - Three parts: module_path.module_name.subhandler (e.g., "arbitium.helper_rds.deletion-protection")

        When a third part (subhandler) is provided, it is automatically injected into the payload
        as the 'subhandler' parameter. This enables multi-function handlers where the agent can
        route to specific functionality using paths like:
        - arbitium/helper_rds/deletion-protection
        - arbitium/helper_rds/create-snapshot
        - arbitium/helper_rds/ls-snapshots
        - arbitium/helper_rds/restore-snapshot
        """
        func_name = "load_and_run"
        _logger_schd.debug("running_handler | %s", module_name)

        try:
            # Handle both file paths and dot-notation module names
            if os.sep in module_name or '/' in module_name:
                # It's a file path - normalize and split using os.sep
                normalized_module_name = os.path.normpath(module_name)
                module_parts = normalized_module_name.split(os.sep)
            else:
                # It's already in dot notation - split by dots
                module_parts = module_name.split('.')

            # Ensure we have at least 2 parts for module_path and module_name
            if len(module_parts) < 2:
                error = f"Module name '{module_name}' must have at least 2 parts (module_path.module_name)"
                return {'success':False,'action':func_name,'error':error,'output':error,'status':500}

            # Extract subhandler from third position if present (backwards compatible)
            subhandler = None
            if len(module_parts) >= 3:
                subhandler = module_parts[2]
                _logger_schd.debug("subhandler_detected | %s", subhandler)
                # Use only the first two parts for module loading
                actual_module_name = '.'.join(module_parts[:2])
            else:
                actual_module_name = module_name

            # Derive class name from the module name (second part), not the subhandler
            class_name = self.convert_module_name_to_class(module_parts[1])
            _logger_schd.debug("loading_class | %s", class_name)

            payload = kwargs.get('payload')  # Extract payload from kwargs
            check = kwargs.get('check',False)

            # Inject subhandler into payload if a third part was provided
            if subhandler is not None:
                if payload is None:
                    payload = {}
                # Only set subhandler if not already present in payload (allow override)
                if 'subhandler' not in payload:
                    payload['subhandler'] = subhandler
                    _logger_schd.debug("subhandler_injected | %s", subhandler)

            instance = self.load_code_class(module_parts[0], module_parts[1], class_name, *args, **kwargs)
            runtime_loaded_class = True

            if not instance:
                error = f"Class '{class_name}' in '{actual_module_name}' could not be loaded."
                return {'success':False,'action':func_name,'error':error,'output':error,'status':500}

            if check:
                if hasattr(instance, "check"):
                    result = instance.check(payload)  # Pass payload to run
                else:
                    error = f"Class '{class_name}' in '{actual_module_name}' has no 'check' method."
                    _logger_schd.error("no_check_method | class=%s | module=%s", class_name, actual_module_name)
                    return {'success':False,'action':func_name,'error':error,'status':500}

            else:
                if hasattr(instance, "run"):
                    result = instance.run(payload)  # Pass payload to run
                else:
                    error = f"Class '{class_name}' in '{actual_module_name}' has no 'run' method."
                    _logger_schd.error("no_run_method | class=%s | module=%s", class_name, actual_module_name)
                    return {'success':False,'action':func_name,'error':error,'status':500}


            if runtime_loaded_class and self.unload_handlers:
                # Unload module to free memory (Lambda / opt-in only; see
                # _unload_handlers_enabled). importlib.import_module reuses the
                # sys.modules entry, so skipping this makes subsequent calls
                # reuse the already-imported module instead of reimporting.
                del instance
                if actual_module_name in sys.modules:
                    del sys.modules[actual_module_name]
                gc.collect()

            if result is None:
                err = (
                    f"Handler '{class_name}' run() returned None; expected a dict "
                    "(e.g. {{'success': True, 'output': ...}})."
                )
                _logger_schd.error("handler_returned_none | class=%s | %s", class_name, err)
                return {
                    'success': False,
                    'action': func_name,
                    'error': err,
                    'output': err,
                    'status': 500,
                }

            if isinstance(result, dict) and 'success' in result and not result['success']:
                return {'success': False, 'action': func_name, 'output': result, 'status': 400}

            return {'success': True, 'action': func_name, 'output': result, 'status': 200}

        except Exception as e:
            tb = traceback.format_exc()
            _logger_schd.error(
                "load_and_run_failed | module=%s | %s\n%s",
                module_name,
                e,
                tb,
            )
            return {
                'success': False,
                'action': func_name,
                'input': module_name,
                'output': f'Error @load_and_run: {str(e)}',
                'exception_type': type(e).__name__,
            }



# Example Usage
if __name__ == "__main__":
    SHL = SchdLoader()

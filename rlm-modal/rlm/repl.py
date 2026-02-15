import sys
import io
import threading
import json
import tempfile
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from rlm import RLM

# Simple sub LM for REPL environment. Note: This could also be just the RLM itself!
class Sub_RLM(RLM):
    """Recursive LLM client for REPL environment with fixed configuration."""

    def __init__(self, model: str = "gpt-5"):
        # Configuration - model can be specified
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

        self.model = model

        # Initialize OpenAI client
        from rlm.utils.llm import OpenAIClient
        self.client = OpenAIClient(api_key=self.api_key, model=model)


    def completion(self, prompt) -> str:
        """
        Simple LM query for sub-LM call.
        """
        try:
            # Handle both string and dictionary/list inputs
            response = self.client.completion(
                messages=prompt,
                timeout=300
            )

            return response

        except Exception as e:
            error_msg = f"Error making LLM query: {str(e)}"
            return error_msg

    def cost_summary(self) -> dict[str, float]:
        raise NotImplementedError("Cost tracking is not implemented for the Sub-RLM.")

    def reset(self):
        raise NotImplementedError("Reset is not implemented for the Sub-RLM.")


class PersistentSandboxSubRLM(RLM):
    """Sub-RLM that runs inside a persistent Modal sandbox.

    The sandbox is created lazily on first call and reused for all subsequent
    llm_query() invocations. Each call does sandbox.exec() with JSON on stdin
    and reads the response from stdout.
    """

    def __init__(
        self,
        model: str = "gpt-5",
        sandbox_image: Optional[str] = None,
        sandbox_volumes: Optional[dict] = None,
        sandbox_workdir: Optional[str] = None,
        env_file_path: Optional[str] = None,
        timeout: int = 600,
        app=None,
    ):
        self.model = model
        self.sandbox_image = sandbox_image
        self.sandbox_volumes = sandbox_volumes or {}
        self.sandbox_workdir = sandbox_workdir
        self.env_file_path = env_file_path
        self.timeout = timeout
        self._app = app  # Pre-looked-up Modal app (optional, avoids redundant lookups in pool)
        self._sandbox = None
        self._call_count = 0
        self._total_exec_time = 0.0

    def _log(self, msg: str):
        """Log to real stdout (bypasses REPL _capture_output)."""
        sys.__stdout__.write(f"{msg}\n")
        sys.__stdout__.flush()

    def _ensure_sandbox(self):
        """Lazily create the Modal sandbox on first use."""
        if self._sandbox is not None:
            return

        import modal

        self._log("[PersistentSandboxSubRLM] Creating persistent Modal sandbox...")

        # Build the sandbox image
        if self.sandbox_image:
            image = modal.Image.from_registry(self.sandbox_image)
        else:
            image = (
                modal.Image.debian_slim(python_version="3.11")
                .pip_install("openai", "python-dotenv")
            )

        # Read env vars from .env file if provided
        sandbox_secrets = []
        env_vars = {}
        if self.env_file_path and os.path.exists(self.env_file_path):
            from dotenv import dotenv_values
            env_vars = dict(dotenv_values(self.env_file_path))
            # Pass as inline secret
            sandbox_secrets.append(modal.Secret.from_dict(env_vars))
        elif os.getenv("OPENAI_API_KEY"):
            # Fall back to local env var
            sandbox_secrets.append(
                modal.Secret.from_dict({"OPENAI_API_KEY": os.getenv("OPENAI_API_KEY")})
            )

        # Copy the worker script into the sandbox image
        worker_path = os.path.join(os.path.dirname(__file__), "sub_rlm_worker.py")
        image = image.add_local_file(worker_path, "/root/sub_rlm_worker.py", copy=True)

        # Reuse pre-looked-up app if provided (e.g. from SandboxPool), otherwise look up
        app = self._app or modal.App.lookup("rlm-sandbox", create_if_missing=True)

        self._sandbox = modal.Sandbox.create(
            image=image,
            secrets=sandbox_secrets,
            timeout=self.timeout,
            app=app,
            **({"workdir": self.sandbox_workdir} if self.sandbox_workdir else {}),
        )
        self._log(f"[PersistentSandboxSubRLM] Sandbox created: {self._sandbox.object_id}")

    def completion(self, prompt) -> str:
        """Run a sub-LLM query inside the persistent sandbox."""
        self._ensure_sandbox()
        self._call_count += 1
        call_num = self._call_count

        # Build the JSON payload
        payload = json.dumps({
            "prompt": prompt,
            "model": self.model,
        })

        # Truncate prompt for logging
        prompt_preview = str(prompt)[:120].replace('\n', ' ')
        self._log(f"[SandboxSubRLM] call #{call_num} | model={self.model} | prompt={prompt_preview}...")

        exec_start = time.time()
        try:
            process = self._sandbox.exec(
                "python",
                "/root/sub_rlm_worker.py",
            )
            # Write payload to stdin, then signal EOF and flush
            process.stdin.write(payload.encode() if isinstance(payload, str) else payload)
            process.stdin.write_eof()
            process.stdin.drain()

            stdout_text = process.stdout.read()
            stderr_text = process.stderr.read()
            exit_code = process.wait()

            elapsed = time.time() - exec_start
            self._total_exec_time += elapsed

            if exit_code != 0:
                self._log(f"[SandboxSubRLM] call #{call_num} FAILED ({elapsed:.1f}s) | exit={exit_code} | stderr={stderr_text[:200]}")
                return f"Error in sandbox exec (exit {exit_code}): {stderr_text}"

            response_preview = stdout_text.strip()[:120].replace('\n', ' ')
            self._log(f"[SandboxSubRLM] call #{call_num} OK ({elapsed:.1f}s) | response={response_preview}...")
            return stdout_text.strip()

        except Exception as e:
            elapsed = time.time() - exec_start
            self._log(f"[SandboxSubRLM] call #{call_num} EXCEPTION ({elapsed:.1f}s) | {e}")
            return f"Error in sandbox sub-RLM: {str(e)}"

    def close(self):
        """Terminate the sandbox."""
        if self._sandbox is not None:
            try:
                self._log(f"[PersistentSandboxSubRLM] Terminating sandbox | total calls={self._call_count} | total exec time={self._total_exec_time:.1f}s")
                self._sandbox.terminate()
            except Exception:
                pass
            self._sandbox = None

    def cost_summary(self) -> dict[str, float]:
        raise NotImplementedError("Cost tracking is not implemented for PersistentSandboxSubRLM.")

    def reset(self):
        raise NotImplementedError("Reset is not implemented for PersistentSandboxSubRLM.")

    def __del__(self):
        self.close()


@dataclass
class REPLResult:
    stdout: str
    stderr: str
    locals: dict
    execution_time: float

    def __init__(self, stdout: str, stderr: str, locals: dict, execution_time: float=None):
        self.stdout = stdout
        self.stderr = stderr
        self.locals = locals
        self.execution_time = execution_time

    def __str__(self):
        return f"REPLResult(stdout={self.stdout}, stderr={self.stderr}, locals={self.locals}, execution_time={self.execution_time})"

class REPLEnv:
    def __init__(
        self,
        recursive_model: str = "gpt-5-mini",
        context_json: Optional[dict | list] = None,
        context_str: Optional[str] = None,
        setup_code: str = None,
        sub_rlm_mode: str = "local",
        sandbox_image: Optional[str] = None,
        sandbox_volumes: Optional[dict] = None,
        sandbox_workdir: Optional[str] = None,
        env_file_path: Optional[str] = None,
        sub_rlm_timeout: int = 600,
        pool_size: int = 4,
    ):
        # Store the original working directory
        self.original_cwd = os.getcwd()

        # Create temporary directory (but don't change global working directory)
        self.temp_dir = tempfile.mkdtemp(prefix="repl_env_")

        # Initialize sub-RLM based on mode
        if sub_rlm_mode == "modal_sandbox":
            self.sub_rlm: RLM = PersistentSandboxSubRLM(
                model=recursive_model,
                sandbox_image=sandbox_image,
                sandbox_volumes=sandbox_volumes,
                sandbox_workdir=sandbox_workdir,
                env_file_path=env_file_path,
                timeout=sub_rlm_timeout,
            )
        else:
            # Default: local Sub_RLM
            self.sub_rlm: RLM = Sub_RLM(model=recursive_model)

        # Create safe globals with only string-safe built-ins
        self.globals = {
            '__builtins__': {
                # Safe built-ins for string manipulation
                'print': print, 'len': len, 'str': str, 'int': int, 'float': float,
                'list': list, 'dict': dict, 'set': set, 'tuple': tuple, 'bool': bool,
                'type': type, 'isinstance': isinstance, 'enumerate': enumerate,
                'zip': zip, 'map': map, 'filter': filter, 'sorted': sorted,
                'min': min, 'max': max, 'sum': sum, 'abs': abs, 'round': round,
                'chr': chr, 'ord': ord, 'hex': hex, 'bin': bin, 'oct': oct,
                'repr': repr, 'ascii': ascii, 'format': format,
                '__import__': __import__,  # Allow imports
                'open': open,  # Allow file access

                # Add commonly used built-ins that were missing
                'any': any, 'all': all, 'hasattr': hasattr, 'getattr': getattr,
                'setattr': setattr, 'delattr': delattr, 'dir': dir, 'vars': vars,
                'range': range,  # Add range function
                'reversed': reversed,  # Add reversed function
                'slice': slice,  # Add slice function
                'iter': iter,  # Add iter function
                'next': next,  # Add next function
                'pow': pow,  # Add pow function
                'divmod': divmod,  # Add divmod function
                'complex': complex,  # Add complex function
                'bytes': bytes,  # Add bytes function
                'bytearray': bytearray,  # Add bytearray function
                'memoryview': memoryview,  # Add memoryview function
                'hash': hash,  # Add hash function
                'id': id,  # Add id function
                'callable': callable,  # Add callable function
                'issubclass': issubclass,  # Add issubclass function
                'super': super,  # Add super function
                'property': property,  # Add property function
                'staticmethod': staticmethod,  # Add staticmethod function
                'classmethod': classmethod,  # Add classmethod function
                'object': object,  # Add object class
                'BaseException': BaseException,  # Add BaseException class
                'ArithmeticError': ArithmeticError,  # Add ArithmeticError class
                'LookupError': LookupError,  # Add LookupError class
                'EnvironmentError': EnvironmentError,  # Add EnvironmentError class
                'AssertionError': AssertionError,  # Add AssertionError class
                'NotImplementedError': NotImplementedError,  # Add NotImplementedError class
                'UnicodeError': UnicodeError,  # Add UnicodeError class
                'Warning': Warning,  # Add Warning class
                'UserWarning': UserWarning,  # Add UserWarning class
                'DeprecationWarning': DeprecationWarning,  # Add DeprecationWarning class
                'PendingDeprecationWarning': PendingDeprecationWarning,  # Add PendingDeprecationWarning class
                'SyntaxWarning': SyntaxWarning,  # Add SyntaxWarning class
                'RuntimeWarning': RuntimeWarning,  # Add RuntimeWarning class
                'FutureWarning': FutureWarning,  # Add FutureWarning class
                'ImportWarning': ImportWarning,  # Add ImportWarning class
                'UnicodeWarning': UnicodeWarning,  # Add UnicodeWarning class
                'BytesWarning': BytesWarning,  # Add BytesWarning class
                'ResourceWarning': ResourceWarning,  # Add ResourceWarning class

                # Add exception classes
                'Exception': Exception, 'ValueError': ValueError, 'TypeError': TypeError,
                'KeyError': KeyError, 'IndexError': IndexError, 'AttributeError': AttributeError,
                'FileNotFoundError': FileNotFoundError, 'OSError': OSError, 'IOError': IOError,
                'RuntimeError': RuntimeError, 'NameError': NameError, 'ImportError': ImportError,
                'StopIteration': StopIteration, 'GeneratorExit': GeneratorExit,
                'SystemExit': SystemExit, 'KeyboardInterrupt': KeyboardInterrupt,

                # Disallow the following built-ins
                'input': None,  # Block input
                'eval': None,  # Block eval
                'exec': None,  # Block exec
                'compile': None,  # Block compile
                'globals': None,  # Block globals access
                'locals': None,  # Block locals access
            }
        }
        self.locals = {}
        self._lock = threading.Lock()
        self.stdout_buffer = io.StringIO()
        self.stderr_buffer = io.StringIO()

        self.load_context(context_json, context_str)

        def llm_query(prompt: str) -> str:
            """Query the LLM with the given prompt."""
            return self.sub_rlm.completion(prompt)

        # Add (R)LM query function to globals
        self.globals['llm_query'] = llm_query

        # Parallel LLM query: lazily creates a SandboxPool on first call
        self._sandbox_pool = None
        _pool_size = pool_size
        _sub_rlm_mode = sub_rlm_mode
        _repl_env = self  # capture for closure

        def parallel_llm_query(prompts: list) -> list:
            """Query an LLM with multiple prompts simultaneously."""
            if _repl_env._sandbox_pool is None and _sub_rlm_mode == "modal_sandbox":
                from rlm.sandbox_pool import SandboxPool
                _repl_env._sandbox_pool = SandboxPool(
                    pool_size=_pool_size,
                    model=recursive_model,
                    sandbox_image=sandbox_image,
                    sandbox_volumes=sandbox_volumes,
                    sandbox_workdir=sandbox_workdir,
                    env_file_path=env_file_path,
                    timeout=sub_rlm_timeout,
                )
            if _repl_env._sandbox_pool is not None:
                return _repl_env._sandbox_pool.parallel_query(prompts)
            else:
                # Local fallback: sequential
                return [_repl_env.sub_rlm.completion(p) for p in prompts]

        self.globals['parallel_llm_query'] = parallel_llm_query

        # Add FINAL_VAR function to globals
        def final_var(variable_name: str) -> str:
            """
            Return the value of a variable from the REPL environment as a final answer.
            This function is used by the model to return variables as final answers.
            """
            # Strip spaces, quotes, and newlines from variable name
            variable_name = variable_name.strip().strip('"').strip("'").strip('\n').strip('\r')
            try:
                # Check if variable exists in locals
                if variable_name in self.locals:
                    value = self.locals[variable_name]
                    return str(value)
                else:
                    return f"Error: Variable '{variable_name}' not found in REPL environment"
            except Exception as e:
                return f"Error retrieving variable '{variable_name}': {str(e)}"

        self.globals['FINAL_VAR'] = final_var

        # Finally, run any setup code if provided
        if setup_code:
            self.code_execution(setup_code)

    def load_context(self, context_json: Optional[dict | list] = None, context_str: Optional[str] = None):
        # Write context JSON to temporary directory using absolute (temp dir) path
        if context_json is not None:
            context_path = os.path.join(self.temp_dir, "context.json")
            with open(context_path, "w") as f:
                json.dump(context_json, f, indent=2)
            context_code = (
                f"import json\n"
                f"with open(r'{context_path}', 'r') as f:\n"
                f"    context = json.load(f)\n"
            )
            self.code_execution(context_code)

        if context_str is not None:
            context_path = os.path.join(self.temp_dir, "context.txt")
            with open(context_path, "w") as f:
                f.write(context_str)
            context_code = (
                f"import os\n"
                f"with open(r'{context_path}', 'r') as f:\n"
                f"    context = f.read()\n"
            )
            self.code_execution(context_code)

    def close(self):
        """Clean up sandbox, pool, and temporary directory."""
        # Clean up sandbox if it's a PersistentSandboxSubRLM
        if hasattr(self.sub_rlm, 'close'):
            self.sub_rlm.close()
        # Clean up sandbox pool if it was created
        if self._sandbox_pool is not None:
            self._sandbox_pool.close()
            self._sandbox_pool = None
        # Clean up temp dir
        try:
            import shutil
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def __del__(self):
        """Clean up temporary directory when object is destroyed"""
        self.close()

    @contextmanager
    def _capture_output(self):
        """Thread-safe context manager to capture stdout/stderr"""
        with self._lock:
            # Store original streams
            old_stdout = sys.stdout
            old_stderr = sys.stderr

            # Create new buffers for this execution
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            try:
                # Redirect streams
                sys.stdout = stdout_buffer
                sys.stderr = stderr_buffer
                yield stdout_buffer, stderr_buffer
            finally:
                # Restore original streams
                sys.stdout = old_stdout
                sys.stderr = old_stderr

    @contextmanager
    def _temp_working_directory(self):
        """Context manager to temporarily change working directory for REPL execution"""
        old_cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir)
            yield
        finally:
            os.chdir(old_cwd)

    def code_execution(self, code) -> REPLResult:
        """
        Simple code execution "notebook-style" in a REPL environment.
        """
        start_time = time.time()
        with self._capture_output() as (stdout_buffer, stderr_buffer):
            with self._temp_working_directory():
                try:
                    # Split code into import statements and other code
                    lines = code.split('\n')
                    import_lines = []
                    other_lines = []

                    for line in lines:
                        if line.startswith(('import ', 'from ')) and not line.startswith('#'):
                            import_lines.append(line)
                        else:
                            other_lines.append(line)

                    # Execute imports first in globals to make them available
                    if import_lines:
                        import_code = '\n'.join(import_lines)
                        exec(import_code, self.globals, self.globals)

                    # Execute the rest of the code. We also want to print last expressions
                    if other_lines:
                        other_code = '\n'.join(other_lines)
                        # Create a combined namespace that includes both globals and locals
                        combined_namespace = {**self.globals, **self.locals}

                        # Check if the last non-comment line is an expression
                        non_comment_lines = [line for line in other_lines if line and not line.startswith('#')]

                        if non_comment_lines:
                            last_line = non_comment_lines[-1]

                            # Check if the last line looks like an expression (not a statement)
                            is_expression = (
                                not last_line.startswith(('import ', 'from ', 'def ', 'class ', 'if ', 'for ', 'while ', 'try:', 'with ', 'return ', 'yield ', 'break', 'continue', 'pass')) and
                                '=' not in last_line.split('#')[0] and  # Not an assignment
                                not last_line.endswith(':') and  # Not a control structure
                                not last_line.startswith('print(')  # Not an explicit print
                            )

                            if is_expression:
                                try:
                                    # Execute all lines except the last one as statements
                                    if len(non_comment_lines) > 1:
                                        # Find where the last line starts in the original code
                                        last_line_start = -1
                                        for i, line in enumerate(other_lines):
                                            if line == last_line:
                                                last_line_start = i
                                                break

                                        if last_line_start > 0:
                                            statements_code = '\n'.join(other_lines[:last_line_start])
                                            exec(statements_code, combined_namespace, combined_namespace)

                                    # Evaluate the last line as an expression and print the result
                                    result = eval(last_line, combined_namespace, combined_namespace)
                                    if result is not None:
                                        print(repr(result))

                                except:
                                    # If evaluation fails, fall back to normal execution
                                    exec(other_code, combined_namespace, combined_namespace)
                            else:
                                # Execute normally as statements
                                exec(other_code, combined_namespace, combined_namespace)
                        else:
                            # Only comments, execute normally (though it won't do anything)
                            exec(other_code, combined_namespace, combined_namespace)

                        # Update locals with any new variables created
                        for key, value in combined_namespace.items():
                            if key not in self.globals:
                                self.locals[key] = value

                    stdout_content = stdout_buffer.getvalue()
                    stderr_content = stderr_buffer.getvalue()
                except Exception as e:
                    stderr_content = stderr_buffer.getvalue() + str(e)
                    stdout_content = stdout_buffer.getvalue()

        end_time = time.time()
        execution_time = end_time - start_time

        # Store output in locals for access
        self.locals['_stdout'] = stdout_content
        self.locals['_stderr'] = stderr_content

        return REPLResult(stdout_content, stderr_content, self.locals.copy(), execution_time)

    def get_cost_summary(self):
        raise NotImplementedError("Cost tracking is not implemented for the REPL Environment.")

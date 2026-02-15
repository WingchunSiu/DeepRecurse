"""
Parallel sandbox pool for concurrent sub-LLM queries.

Creates N persistent Modal sandboxes and dispatches prompts round-robin
across them using a ThreadPoolExecutor.
"""

import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


class SandboxPool:
    """Pool of PersistentSandboxSubRLM instances for parallel llm_query calls."""

    def __init__(
        self,
        pool_size: int = 4,
        model: str = "gpt-5",
        sandbox_image: Optional[str] = None,
        sandbox_volumes: Optional[dict] = None,
        sandbox_workdir: Optional[str] = None,
        env_file_path: Optional[str] = None,
        timeout: int = 600,
    ):
        self.pool_size = pool_size
        self.model = model
        self.sandbox_image = sandbox_image
        self.sandbox_volumes = sandbox_volumes
        self.sandbox_workdir = sandbox_workdir
        self.env_file_path = env_file_path
        self.timeout = timeout

        self._sandboxes: list = []
        self._locks: list[threading.Lock] = []
        self._ready = False
        self._init_lock = threading.Lock()

    def _log(self, msg: str):
        """Log to real stdout (bypasses REPL capture)."""
        sys.__stdout__.write(f"[SandboxPool] {msg}\n")
        sys.__stdout__.flush()

    def _ensure_pool(self):
        """Lazily create all N sandboxes in parallel."""
        if self._ready:
            return

        with self._init_lock:
            if self._ready:
                return

            import modal
            from rlm.repl import PersistentSandboxSubRLM

            self._log(f"Creating {self.pool_size} sandboxes in parallel...")
            start = time.time()

            # Look up the app once, share across all sandboxes
            app = modal.App.lookup("rlm-sandbox", create_if_missing=True)

            def _create_sandbox(idx: int) -> PersistentSandboxSubRLM:
                s = PersistentSandboxSubRLM(
                    model=self.model,
                    sandbox_image=self.sandbox_image,
                    sandbox_volumes=self.sandbox_volumes,
                    sandbox_workdir=self.sandbox_workdir,
                    env_file_path=self.env_file_path,
                    timeout=self.timeout,
                    app=app,
                )
                # Force sandbox creation now (not lazily on first call)
                s._ensure_sandbox()
                self._log(f"sandbox[{idx}] ready: {s._sandbox.object_id}")
                return s

            with ThreadPoolExecutor(max_workers=self.pool_size) as executor:
                futures = [executor.submit(_create_sandbox, i) for i in range(self.pool_size)]
                self._sandboxes = [f.result() for f in futures]

            self._locks = [threading.Lock() for _ in range(self.pool_size)]
            elapsed = time.time() - start
            self._log(f"{self.pool_size} sandboxes ready in {elapsed:.1f}s")
            self._ready = True

    def _query_one(self, sandbox_idx: int, prompt: str, prompt_idx: int) -> str:
        """Query a single sandbox with locking and retry-on-error."""
        sandbox = self._sandboxes[sandbox_idx]
        lock = self._locks[sandbox_idx]

        with lock:
            try:
                result = sandbox.completion(prompt)
                return result
            except Exception as e:
                self._log(f"sandbox[{sandbox_idx}] error on prompt {prompt_idx}: {e}, recreating...")
                try:
                    sandbox.close()
                except Exception:
                    pass

                # Recreate this sandbox
                import modal
                from rlm.repl import PersistentSandboxSubRLM

                app = modal.App.lookup("rlm-sandbox", create_if_missing=True)
                new_sandbox = PersistentSandboxSubRLM(
                    model=self.model,
                    sandbox_image=self.sandbox_image,
                    sandbox_volumes=self.sandbox_volumes,
                    sandbox_workdir=self.sandbox_workdir,
                    env_file_path=self.env_file_path,
                    timeout=self.timeout,
                    app=app,
                )
                new_sandbox._ensure_sandbox()
                self._sandboxes[sandbox_idx] = new_sandbox

                # Retry once
                try:
                    return new_sandbox.completion(prompt)
                except Exception as e2:
                    return f"Error in sandbox pool (retry failed): {e2}"

    def parallel_query(self, prompts: list[str]) -> list[str]:
        """Dispatch prompts round-robin across sandboxes, return results in order."""
        self._ensure_pool()

        self._log(f"Dispatching {len(prompts)} prompts across {self.pool_size} sandboxes")
        start = time.time()

        with ThreadPoolExecutor(max_workers=self.pool_size) as executor:
            futures = []
            for i, prompt in enumerate(prompts):
                sandbox_idx = i % self.pool_size
                futures.append(executor.submit(self._query_one, sandbox_idx, prompt, i))

            results = [f.result() for f in futures]

        elapsed = time.time() - start
        self._log(f"All {len(prompts)} prompts completed in {elapsed:.1f}s")
        return results

    def close(self):
        """Terminate all sandboxes in the pool."""
        for i, sandbox in enumerate(self._sandboxes):
            try:
                sandbox.close()
                self._log(f"sandbox[{i}] terminated")
            except Exception:
                pass
        self._sandboxes = []
        self._locks = []
        self._ready = False

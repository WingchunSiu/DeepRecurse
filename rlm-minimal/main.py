from rlm.rlm_repl import RLM_REPL
from rlm.monocontext import Monocontext

def main():
    print("Example of using RLM (REPL) with GPT-5-nano on a needle-in-haystack problem.")
    print("[main] Initializing S3-backed Monocontext")
    context = Monocontext(
        bucket="test-monoctx",
        prefix="",
        manifest_name="manifest.json",
        enable_logging=False,
    )
    print("[main] Loading manifest via len(context)")
    total_lines = len(context)
    print(f"[main] Context ready; total_lines={total_lines}")

    rlm = RLM_REPL(
        model="gpt-5",
        recursive_model="gpt-5-nano",
        enable_logging=True,
        max_iterations=10
    )
    print("[main] RLM initialized")
    query = "I'm looking for a magic number. What is it?"
    print(f"[main] Starting RLM completion; query={query!r}")
    result = rlm.completion(context=context, query=query)
    print(f"Result: {result}")

if __name__ == "__main__":
    main()

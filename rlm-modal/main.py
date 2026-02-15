"""
NIAH (Needle in a Haystack) test for RLM with optional persistent Modal sandbox.

Usage:
    python main.py --mode local          # Run purely local (same as rlm-minimal-modded)
    python main.py --mode modal          # Run with persistent Modal sandbox for llm_query
    python main.py --mode modal --num-lines 100000  # Smaller haystack
"""

import argparse
import random
import sys
import os

# Add parent so `rlm` package is importable when running from rlm-modal/
sys.path.insert(0, os.path.dirname(__file__))

from rlm.rlm_repl import RLM_REPL


def generate_massive_context(num_lines: int = 100_000, answer: str = "1298418") -> str:
    """Generate a haystack with a magic number needle."""
    print(f"Generating context with {num_lines:,} lines...")

    random_words = ["blah", "random", "text", "data", "content", "information", "sample"]

    lines = []
    for _ in range(num_lines):
        num_words = random.randint(3, 8)
        line_words = [random.choice(random_words) for _ in range(num_words)]
        lines.append(" ".join(line_words))

    # Insert the magic number somewhere in the middle third
    lo = num_lines // 3
    hi = 2 * num_lines // 3
    magic_position = random.randint(lo, hi)
    lines[magic_position] = f"The magic number is {answer}"

    print(f"Magic number inserted at line {magic_position:,}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="RLM NIAH test with optional Modal sandbox")
    parser.add_argument(
        "--mode",
        choices=["local", "modal"],
        default="local",
        help="Sub-RLM execution mode: 'local' (default) or 'modal' (persistent sandbox)",
    )
    parser.add_argument("--num-lines", type=int, default=100_000, help="Number of haystack lines")
    parser.add_argument("--model", type=str, default="gpt-5", help="Root LM model")
    parser.add_argument("--recursive-model", type=str, default="gpt-5-nano", help="Sub-RLM model")
    parser.add_argument("--max-iterations", type=int, default=10, help="Max root LM iterations")
    parser.add_argument("--env-file", type=str, default=None, help="Path to .env file for sandbox secrets")
    parser.add_argument("--logging", action="store_true", default=True, help="Enable verbose logging (default: on)")
    parser.add_argument("--pool-size", type=int, default=4, help="Number of parallel sandboxes for parallel_llm_query (default: 4)")
    parser.add_argument("--force-parallel", action="store_true", help="Use system prompt that forces parallel_llm_query usage")
    parser.add_argument("--no-logging", action="store_true", help="Disable verbose logging")
    args = parser.parse_args()

    enable_logging = args.logging and not args.no_logging

    answer = str(random.randint(1_000_000, 9_999_999))
    context = generate_massive_context(num_lines=args.num_lines, answer=answer)

    sub_rlm_mode = "modal_sandbox" if args.mode == "modal" else "local"

    rlm = RLM_REPL(
        model=args.model,
        recursive_model=args.recursive_model,
        enable_logging=enable_logging,
        max_iterations=args.max_iterations,
        sub_rlm_mode=sub_rlm_mode,
        env_file_path=args.env_file,
        pool_size=args.pool_size,
        force_parallel=args.force_parallel,
    )

    query = "I'm looking for a magic number. What is it?"
    print(f"\nRunning RLM in '{args.mode}' mode...")
    print(f"Root model: {args.model}, Sub-RLM model: {args.recursive_model}")
    print(f"Context: {len(context):,} chars, {args.num_lines:,} lines\n")

    result = rlm.completion(context=context, query=query)

    print(f"\n{'='*60}")
    print(f"Result:   {result}")
    print(f"Expected: {answer}")
    print(f"Match:    {answer in str(result)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

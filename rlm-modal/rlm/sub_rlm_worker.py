#!/usr/bin/env python3
"""
Sub-RLM worker script that runs inside a Modal sandbox.

Reads a JSON payload from stdin:
    {"prompt": <str or list>, "model": <str>}

Calls OpenAI and writes the response text to stdout.
"""

import sys
import json
import os

def main():
    # Read JSON payload from stdin
    raw = sys.stdin.read()
    payload = json.loads(raw)

    prompt = payload["prompt"]
    model = payload.get("model", "gpt-5")

    # Build OpenAI client
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in sandbox environment", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    # Normalize prompt to messages list
    if isinstance(prompt, str):
        messages = [{"role": "user", "content": prompt}]
    elif isinstance(prompt, dict):
        messages = [prompt]
    elif isinstance(prompt, list):
        messages = prompt
    else:
        print(f"ERROR: Unsupported prompt type: {type(prompt)}", file=sys.stderr)
        sys.exit(1)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        result = response.choices[0].message.content
        # Write result to stdout (no trailing newline to keep it clean)
        sys.stdout.write(result)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

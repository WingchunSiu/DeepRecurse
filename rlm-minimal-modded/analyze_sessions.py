"""
RLM-based session analyzer.
Feeds saved conversation files through RLM to extract structured context summaries.

Usage:
    export OPENAI_API_KEY="sk-..."
    python analyze_sessions.py
"""
import os
import sys
from rlm.rlm_repl import RLM_REPL

CHAT_FILES = [
    "chat_airsim.txt",
    # "chat_isaac_sim.txt",  # uncomment for full run
]

# For quick testing, truncate to first N chars (0 = no truncation)
TRUNCATE_CHARS = 50_000

EXTRACTION_QUERY = """Analyze this conversation and extract a structured summary.

IMPORTANT INSTRUCTIONS FOR USING THE REPL:
1. First, check len(context) to see how big it is.
2. Feed the ENTIRE context into ONE llm_query call — it can handle up to 500K chars.
   Do it like this:
   ```repl
   summary = llm_query("Analyze this conversation and produce a structured markdown summary with these sections: Session Overview (main task, who was involved), Key Decisions Made (concrete decisions with rationale), Current State (what works, what's broken/blocked), Open Threads / Next Steps (unfinished work, planned actions), Important Code/Config/Commands (critical snippets, paths, commands), Context for Next Session (what someone needs to know to continue). Be concise, skip debugging back-and-forth, focus on signal. Here is the conversation:\\n\\n" + context)
   print(summary[:500])
   ```
3. Then IMMEDIATELY return it:
   ```repl
   FINAL_VAR('summary')
   ```

Do NOT make more than 2 llm_query calls. Do NOT extract sections one by one.
The sub-LLM is powerful enough to handle the full context in one call."""


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: Set OPENAI_API_KEY environment variable first.")
        print("  export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    # Use gpt-4o-mini for cost efficiency; bump to gpt-4o for higher quality
    rlm = RLM_REPL(
        model="gpt-5-mini",
        recursive_model="gpt-5-nano",
        enable_logging=True,
        max_iterations=5,
    )

    results = []
    for fname in CHAT_FILES:
        if not os.path.exists(fname):
            print(f"WARNING: {fname} not found, skipping.")
            continue

        with open(fname, "r") as f:
            chat_content = f.read()

        if TRUNCATE_CHARS > 0:
            chat_content = chat_content[:TRUNCATE_CHARS]

        char_count = len(chat_content)
        print(f"\n{'='*60}")
        print(f"Processing: {fname} ({char_count:,} chars)")
        print(f"{'='*60}")

        result = rlm.completion(context=chat_content, query=EXTRACTION_QUERY)
        results.append(f"# From: {fname}\n\n{result}\n")
        rlm.reset()

    if not results:
        print("ERROR: No chat files found to process.")
        sys.exit(1)

    combined = f"""# Session Context (Auto-generated via RLM)
# Generated from {len(results)} previous conversations

{"---\n\n".join(results)}
---
# End of context. Start your new session with this loaded.
"""

    output_path = "context_summary.md"
    with open(output_path, "w") as f:
        f.write(combined)

    print(f"\n{'='*60}")
    print(f"Done! Context summary written to {output_path}")
    print(f"Total size: {len(combined):,} chars")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

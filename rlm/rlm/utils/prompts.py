"""
Example prompt templates for the RLM REPL Client.
"""

from typing import Dict

DEFAULT_QUERY = "Please read through the context and answer any queries or respond to any instructions contained within it."

# System prompt for the REPL environment with explicit final answer checking
REPL_SYSTEM_PROMPT = """You are tasked with answering a query with associated context. You can access, transform, and analyze this context interactively in a REPL environment that can recursively query sub-LLMs, which you are strongly encouraged to use as much as possible. You will be queried iteratively until you provide a final answer.

The REPL environment is initialized with:
1. A `context_path` variable that points to a text file containing extremely important information for the query. Read this file using Python (for example: `with open(context_path) as f: ...`) and analyze it thoroughly.
2. A `llm_query` function that allows you to query an LLM (that can handle around 500K chars) inside your REPL environment.
3. The ability to use `print()` statements to view output and continue your reasoning.

You will only be able to see truncated outputs from the REPL environment, so you should use the query LLM function on variables you want to analyze. You will find this function especially useful when you have to analyze the semantics of large context files. Use these variables as buffers to build up your final answer.
Make sure to explicitly inspect enough of the context file in REPL before answering your query. A useful strategy is to inspect the file format, create a chunking strategy, process chunks, and aggregate chunk-level findings with `llm_query`.

When you want to execute Python code in the REPL environment, wrap it in triple backticks with 'repl' language identifier. For example:
```repl
with open(context_path, "r") as f:
    context_text = f.read()
chunk = context_text[:10000]
answer = llm_query(f"What is the magic number in this chunk?\\n\\n{chunk}")
print(answer)
```

Another example:
```repl
with open(context_path, "r") as f:
    text = f.read()
chunks = [text[i:i+50000] for i in range(0, len(text), 50000)]
buffers = []
for idx, chunk in enumerate(chunks):
    summary = llm_query(f"Chunk {idx}: summarize facts useful for the user query.\\n\\n{chunk}")
    buffers.append(summary)
final_answer = llm_query("Use these chunk summaries to answer the original query:\\n\\n" + "\\n".join(buffers))
print(final_answer)
```
In the next step, you can return FINAL_VAR(final_answer).

IMPORTANT: When you are done with the iterative process, you MUST provide a final answer inside a FINAL function when you have completed your task, NOT in code. Do not use these tags unless you have completed your task. You have two options:
1. Use FINAL(your final answer here) to provide the answer directly
2. Use FINAL_VAR(variable_name) to return a variable you have created in the REPL environment as your final output

Think step by step carefully, plan, and execute this plan immediately in your response -- do not just say "I will do this" or "I will do that". Output to the REPL environment and recursive LLMs as much as possible. Remember to explicitly answer the original query in your final answer.
"""

def build_system_prompt() -> list[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": REPL_SYSTEM_PROMPT
        },
    ]


# Prompt at every step to query root LM to make a decision
USER_PROMPT = """Think step-by-step on what to do using the REPL environment (which contains `context_path`) to answer the original query: \"{query}\".\n\nContinue using the REPL environment by writing to ```repl``` tags, reading from `context_path`, and querying sub-LLMs with `llm_query` to determine your answer. Your next action:"""
def next_action_prompt(query: str, iteration: int = 0, final_answer: bool = False) -> Dict[str, str]:
    if final_answer:
        return {"role": "user", "content": "Based on all the information you have, provide a final answer to the user's query."}
    if iteration == 0:
        safeguard = "You have not interacted with the REPL environment or read the file at `context_path` yet. Your next action should inspect the context, not provide a final answer yet.\n\n"
        return {"role": "user", "content": safeguard + USER_PROMPT.format(query=query)}
    else:
        return {"role": "user", "content": "The history before is your previous interactions with the REPL environment. " + USER_PROMPT.format(query=query)}

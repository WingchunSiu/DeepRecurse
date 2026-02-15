#!/bin/bash
# Hook script: auto-upload session transcript to Modal volume on session end
# Called by Claude Code SessionEnd hook

# Read hook input from stdin (JSON with session info)
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -n "$SESSION_ID" ]; then
    python3 "$SCRIPT_DIR/upload_context.py" "$SESSION_ID" >> /tmp/rlm-session-upload.log 2>&1
else
    # Fallback: upload latest
    python3 "$SCRIPT_DIR/upload_context.py" >> /tmp/rlm-session-upload.log 2>&1
fi

exit 0

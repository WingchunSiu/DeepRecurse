---
name: upload-context
description: Upload current Claude Code session transcript to Modal volume for RLM processing
user_invocable: true
---

Upload the current session's conversation transcript to the Modal volume `rlm-context`.

Run the upload script:
```bash
python3 /Users/dmytro/Desktop/Gits/rlm-explorations/scripts/upload_context.py
```

If the user specifies a session ID, pass it as an argument:
```bash
python3 /Users/dmytro/Desktop/Gits/rlm-explorations/scripts/upload_context.py <session-id>
```

If the user says "all", upload all sessions:
```bash
python3 /Users/dmytro/Desktop/Gits/rlm-explorations/scripts/upload_context.py --all
```

After uploading, confirm what was uploaded and the volume path.

#!/bin/bash
INPUT=$(cat)
echo "$INPUT" | jq -r '.last_assistant_message // ""' > /tmp/claude_last_response
exit 0

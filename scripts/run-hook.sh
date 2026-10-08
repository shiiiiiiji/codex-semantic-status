#!/bin/sh
# Always return silent successful Stop JSON, including a missing Python runtime.
semantic_plugin_root=${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}
if [ -z "$semantic_plugin_root" ]; then
  semantic_plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." 2>/dev/null && pwd)
fi
for semantic_python in "${SEMANTIC_STATUS_PYTHON:-python3}" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if "$semantic_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
    "$semantic_python" "$semantic_plugin_root/scripts/semantic_status.py" hook 2>/dev/null || printf '{}\n'
    exit 0
  fi
done
printf '{}\n'
exit 0

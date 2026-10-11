#!/bin/bash
# Evidence capture: print what the agent should review. Runs before the agent; keep it read-only and fast.
echo "=== host $(hostname) $(date -Is) ==="
echo "--- 1. disk ---"
df -h / 2>&1 | tail -2 || echo "MISSING: df"
echo "--- 2. load ---"
cat /proc/loadavg 2>/dev/null || uptime || echo "MISSING: load"
echo "--- 3. failed units ---"
systemctl --user --failed --no-legend 2>/dev/null || echo "(systemctl --user unavailable)"

#!/bin/sh
set -e
# MCP proxy on 3001 (internal), auth proxy on 3000 (external)
mcp-streamablehttp-proxy --host 127.0.0.1 --port 3001 node dist/index.js &
PID=$!
sleep 2
exec python -m uvicorn auth_proxy:app --host 0.0.0.0 --port 3000

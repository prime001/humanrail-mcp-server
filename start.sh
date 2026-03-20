#!/bin/bash
# Start HumanRail MCP Server in HTTP mode on port 8100
cd "$(dirname "$0")"
exec venv/bin/python3 -c "
from server import mcp
mcp.run(transport='streamable-http', host='127.0.0.1', port=8100)
"

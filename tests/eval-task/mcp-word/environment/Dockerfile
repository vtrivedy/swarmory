FROM python:3.12-slim-bookworm
# The environment's MCP server: the task's tool for the agent, declared in task.toml's [[environment.mcp_servers]].
COPY server.py /opt/env-mcp/server.py
RUN useradd --create-home agent && mkdir -p /app/output && chown agent /app/output
WORKDIR /app
USER agent

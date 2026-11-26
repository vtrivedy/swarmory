FROM python:3.12-slim
# Pin the base image by digest before you measure in Phase 5.
# For no-network runs with an offline agent CLI (such as claude-code or codex),
# preinstall and pin that CLI here as shown in references/environment.md.
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates curl procps ripgrep \
	&& rm -rf /var/lib/apt/lists/*
RUN useradd --create-home agent && mkdir -p /app/output && chown agent /app/output
WORKDIR /app
USER agent

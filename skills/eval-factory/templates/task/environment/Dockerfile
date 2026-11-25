FROM node:22-bookworm-slim
# The solver has no network, so Harbor can't install a vendor harness at setup. Install each one here, pinned when
# the task is frozen. Harbor skips its own install when the binary is on PATH. Node comes from the base image: Debian's
# nodejs and npm packages made a cold build outlast Harbor's 10-minute build limit.
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates curl procps python3 ripgrep \
	&& npm install -g @anthropic-ai/claude-code @openai/codex && rm -rf /var/lib/apt/lists/* /root/.npm
RUN useradd --create-home agent && mkdir -p /app/output && chown agent /app/output
WORKDIR /app
USER agent

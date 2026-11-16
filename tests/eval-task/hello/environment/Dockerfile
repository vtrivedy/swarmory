# Modal builds this image and needs Python and pip in it, so it starts from Python and adds Node.
FROM python:3.12-slim-bookworm
# The solver has no network, so each harness is installed here, where Harbor finds it on PATH.
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates curl procps \
	&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y --no-install-recommends nodejs \
	&& npm install -g @anthropic-ai/claude-code @openai/codex && rm -rf /var/lib/apt/lists/* /root/.npm
RUN useradd --create-home agent && mkdir -p /app/output && chown agent /app/output
WORKDIR /app
USER agent

#!/bin/sh
# Harbor with its Modal extra and pytest in one Python, run as harbor-python, and harbor on PATH. Safe to run twice.
set -e
if command -v uv > /dev/null; then  # a local run: into this agent's home
  uv tool install -q --with pytest 'harbor[modal]==0.27.0'
  printf '#!/bin/sh\nexec "%s" "$@"\n' "$(uv tool dir)/harbor/bin/python" > "$HOME/.local/bin/harbor-python"
  chmod +x "$HOME/.local/bin/harbor-python"  # a script, not a link, so Python finds the tool's environment
else  # a Modal agent, whose sandbox has no uv
  python3 -m pip install -q 'harbor[modal]==0.27.0' pytest
  ln -sf "$(command -v python3)" /usr/local/bin/harbor-python
fi

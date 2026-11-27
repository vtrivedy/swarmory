#!/bin/sh
# Harbor with its Modal extra and pytest in one Python, run as harbor-python, and harbor on PATH. Safe to run twice.
# On Modal, image.sh has installed them already.
set -e
command -v harbor-python > /dev/null && exit 0
uv tool install -q --with pytest 'harbor[modal]==0.27.0'
printf '#!/bin/sh\nexec "%s" "$@"\n' "$(uv tool dir)/harbor/bin/python" > "$HOME/.local/bin/harbor-python"
chmod +x "$HOME/.local/bin/harbor-python"  # a script, not a link, so Python finds the tool's environment

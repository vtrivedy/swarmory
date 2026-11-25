#!/bin/sh
# Harbor with its Modal extra and pytest in one Python, run as harbor-python, and harbor on PATH. Both go in uv's
# tool bin folder, beside the tool. Safe to run twice. It installs from PyPI: on Modal, start the run with
# --allow pypi.org --allow files.pythonhosted.org.
set -e
env -u UV_OFFLINE uv tool install -q --with pytest 'harbor[modal]==0.23.0'  # Deep Agents sets UV_OFFLINE
bin=$(uv tool dir --bin)
mkdir -p "$bin"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$(uv tool dir)/harbor/bin/python" > "$bin/harbor-python"
chmod +x "$bin/harbor-python"  # a script, not a link, so Python finds the tool's environment

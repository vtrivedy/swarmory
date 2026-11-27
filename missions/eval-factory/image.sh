#!/bin/sh
# On Modal: Harbor with its Modal extra and pytest in the image's Python, run as harbor-python, and harbor on PATH.
pip install -q 'harbor[modal]==0.27.0' pytest
ln -sf "$(command -v python3)" /usr/local/bin/harbor-python

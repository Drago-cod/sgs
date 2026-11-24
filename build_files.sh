#!/bin/bash
set -e

PYTHON_BIN=$(command -v python || command -v python3)

"$PYTHON_BIN" -m pip install --no-cache-dir -r requirements.txt
"$PYTHON_BIN" manage.py collectstatic --noinput
"$PYTHON_BIN" manage.py migrate --noinput
"$PYTHON_BIN" manage.py ensure_admin

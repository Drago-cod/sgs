#!/bin/bash
set -e

python3 -m pip install --no-cache-dir -r requirements.txt

python3 manage.py collectstatic --noinput

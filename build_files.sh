#!/bin/bash
set -o errexit

# Install dependencies
python3 -m pip install --no-cache-dir -r requirements.txt

# Collect static files
python3 manage.py collectstatic --noinput#!/bin/bash

# Install Python dependencies
python3 -m pip install --no-cache-dir -r requirements.txt

# Create staticfiles directory
mkdir -p staticfiles

# Collect static files
python3 manage.py collectstatic --noinput --clear

# Copy static files to staticfiles directory
cp -r static/* staticfiles/ 2>/dev/null || :

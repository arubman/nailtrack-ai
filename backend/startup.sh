#!/usr/bin/env bash
# Azure App Service startup. Oryx installs requirements.txt first, which pulls
# opencv-contrib-python via MediaPipe. This script then installs the EGL library
# MediaPipe's native code needs and puts opencv-python-headless back on top.
set -euo pipefail
cd "$(dirname "$0")"

if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y -qq libegl1 libgl1 libgles2 libglib2.0-0 libgomp1 || echo "Could not apt-install MediaPipe libraries; continuing."
fi

python -m pip install --force-reinstall --no-deps "opencv-python-headless==5.0.0.93"

exec gunicorn \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 5 \
  --forwarded-allow-ips="*" \
  --access-logfile - \
  --error-logfile - \
  app.main:app

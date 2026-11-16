# API image for Azure Container Apps. The same image runs on App Service
# when a paid plan is available. MediaPipe's libraries and the hand landmarker
# model are baked in, so the container does not apt-install at boot.
# Headless OpenCV is installed after requirements.txt because MediaPipe
# depends on opencv-contrib-python.
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libegl1 \
        libgl1 \
        libgles2 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --force-reinstall --no-deps "opencv-python-headless==5.0.0.93"

COPY . .

# One worker by default. SQLite on an Azure Files share cannot take concurrent
# writers, so Container Apps sets WEB_CONCURRENCY=1. Raise it only for Postgres.
ENV PORT=8000 \
    WEB_CONCURRENCY=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8000

# Do not use startup.sh here: that script is the zip-deploy fallback and would apt-get at boot.
CMD ["bash", "-lc", "exec gunicorn --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --worker-class uvicorn.workers.UvicornWorker --timeout 120 --graceful-timeout 30 --keep-alive 5 --forwarded-allow-ips=* --access-logfile - --error-logfile - app.main:app"]

# Recommended App Service path. Libraries MediaPipe needs are in the image,
# so startup does not apt-install. Headless OpenCV is installed after
# requirements.txt because MediaPipe depends on opencv-contrib-python.
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

ENV PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000

# Do not use startup.sh here: that script is the zip-deploy fallback and would apt-get at boot.
CMD ["bash", "-lc", "exec gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --graceful-timeout 30 --keep-alive 5 --forwarded-allow-ips=* --access-logfile - --error-logfile - app.main:app"]

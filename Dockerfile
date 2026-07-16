FROM python:3.11-slim

WORKDIR /app

# Layer-cache the dependency install: only re-runs when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 127.0.0.1 (the serve.py default) is unreachable from outside the container.
ENV ASO_V3_HOST=0.0.0.0 \
    PORT=5178 \
    PYTHONUNBUFFERED=1

EXPOSE 5178

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"5178\")}/v3/health')" || exit 1

CMD ["python", "serve.py"]
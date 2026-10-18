FROM python:3.11-slim

ARG INSTALL_BROWSER=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Self-VCC channel only. The default image stays small; enable explicitly:
#   INSTALL_BROWSER=true docker compose build worker
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
      playwright install --with-deps chromium && chmod -R 755 /ms-playwright; \
    fi

COPY app ./app

RUN useradd --create-home --uid 10001 moliply \
    && chown -R moliply:moliply /app
USER moliply

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

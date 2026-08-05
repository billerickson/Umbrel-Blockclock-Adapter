FROM python:3.12-alpine

ARG BLOCKCLOCK_ADAPTER_VERSION=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLOCKCLOCK_ADAPTER_VERSION=${BLOCKCLOCK_ADAPTER_VERSION}

RUN addgroup -S blockclock && adduser -S -G blockclock blockclock

WORKDIR /app
COPY blockclock_adapter /app/blockclock_adapter

USER blockclock
EXPOSE 21022

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:21022/health', timeout=3)"]

CMD ["python", "-m", "blockclock_adapter.app"]

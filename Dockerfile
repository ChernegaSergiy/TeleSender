# syntax=docker/dockerfile:1

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# cloudflared binary for the optional HTTPS tunnel.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) binary=cloudflared-linux-amd64 ;; \
        arm64) binary=cloudflared-linux-arm64 ;; \
        armhf) binary=cloudflared-linux-arm ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/$binary" -o /usr/local/bin/cloudflared; \
    chmod +x /usr/local/bin/cloudflared; \
    rm -rf /var/lib/apt/lists/*

# Application code.
COPY main.py ./
COPY src ./src
COPY core ./core
COPY languages ./languages
COPY assets ./assets

# Runtime state (Telegram sessions, SQLite DB, logs, plugins, gallery).
RUN mkdir -p /app/data /app/plugins /app/gallery \
    && useradd --create-home --uid 1000 --user-group --shell /usr/sbin/nologin telesender \
    && chown -R telesender:telesender /app

USER telesender

EXPOSE 8000

CMD ["python", "main.py"]

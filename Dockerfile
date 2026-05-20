# syntax=docker/dockerfile:1.4
# BuildKit (Railway): caché apt en tmpfs evita "not enough free space in /var/cache/apt/archives/"
FROM python:3.11-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Sin retener .deb en disco; respaldo si tmpfs no está disponible
RUN printf '%s\n' \
    'APT::Keep-Downloaded-Packages "false";' \
    'Dir::Cache::archives "/tmp/apt-arch";' \
    > /etc/apt/apt.conf.d/99-docker-lean

# LibreOffice Writer (DOCX → PDF) — instalación mínima
RUN --mount=type=tmpfs,target=/var/cache/apt \
    --mount=type=tmpfs,target=/var/lib/apt/lists \
    --mount=type=tmpfs,target=/tmp/apt-arch \
    set -eux; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /tmp/apt-arch/*; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-liberation \
        fonts-dejavu-core \
        fontconfig \
    ; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /tmp/apt-arch/*

RUN set -eux; \
    SOFFICE="$(command -v soffice || command -v libreoffice)"; \
    test -n "$SOFFICE"; \
    "$SOFFICE" --version

WORKDIR /app

RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080}"]

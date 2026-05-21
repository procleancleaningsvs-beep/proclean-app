FROM python:3.11-slim-bookworm

ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN mkdir -p /app/data

RUN apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-liberation \
        fonts-dejavu-core \
        fontconfig \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN which soffice || which libreoffice

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080}"]

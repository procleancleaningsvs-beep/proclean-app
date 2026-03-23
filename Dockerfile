FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-core libreoffice-writer libreoffice-common fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .

ENV PROCLEAN_INSTANCE_DIR=/data/instance \
    PROCLEAN_GENERATED_DIR=/data/generated \
    PROCLEAN_TEMPLATES_DIR=/data/docx_templates \
    PORT=5000

RUN mkdir -p /data/instance /data/generated /data/docx_templates
EXPOSE 5000
CMD sh -c "gunicorn --bind 0.0.0.0:${PORT} app:app"

FROM python:3.11-slim

# LibreOffice Writer mínimo para conversión DOCX → PDF (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    fonts-liberation \
    fonts-dejavu-core \
    fontconfig \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*

WORKDIR /app

# Ruta preparada para montar Volume en Railway.
RUN mkdir -p /app/data

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8080

CMD ["python", "app.py"]

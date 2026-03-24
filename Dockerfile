FROM python:3.11-slim

# Instalar LibreOffice
RUN apt-get update && apt-get install -y \
    libreoffice \
    libreoffice-writer \
    fonts-liberation \
    && apt-get clean

WORKDIR /app

# Ruta preparada para montar Volume en Railway.
RUN mkdir -p /app/data

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8080

CMD ["python", "app.py"]

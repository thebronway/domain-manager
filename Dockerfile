# Use a lean Python base image
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    cron \
    certbot \
    python3-certbot-dns-route53 \
    curl \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /config /certs /logs \
    && chown -R www-data:www-data /config /certs /logs
RUN chmod -R 755 /config /certs /logs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- FIX: Copy directly to /app to avoid double nesting ---
COPY . /app

# If entrypoint exists in this folder, copy it to root
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN chown -R www-data:www-data /app

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
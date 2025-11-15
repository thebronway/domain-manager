# Use a lean Python base image
FROM python:3.11-slim-bookworm

# Set environment variables (Updated Format)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cron \
    certbot \
    python3-certbot-dns-route53 \
    curl \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Create application directories
WORKDIR /app
RUN mkdir -p /config /certs /logs \
    && chown -R www-data:www-data /config /certs /logs
RUN chmod -R 755 /config /certs /logs

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ./app /app/app
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set permissions for the app directory
RUN chown -R www-data:www-data /app

# Expose the web server port
EXPOSE 8080

# Run the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
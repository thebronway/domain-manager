#!/bin/sh

# Start the cron daemon in the background (as root)
cron

# Create the log file AND set permissions on the entire directory.
# This ensures the 'www-data' user can write AND rotate logs.
touch /logs/domain-manager.log
chown -R www-data:www-data /logs

# Create the state file AND set permissions on the config directory.
touch /config/app_state.json
chown -R www-data:www-data /config

# Set permissions for the /certs directory
# This is needed so www-data can create new domain folders
echo "Setting permissions for /certs..."
chown -R www-data:www-data /certs

# Set gunicorn web server options
# We use a single worker to prevent duplicate schedulers
GUNICORN_CMD_ARGS="--bind 0.0.0.0:8080 --workers 1 --threads 4 --timeout 120 --user www-data --group www-data"

# Start the main Python application using gunicorn
# Gunicorn will drop privileges to www-data itself
exec gunicorn "app.app:app" $GUNICORN_CMD_ARGS
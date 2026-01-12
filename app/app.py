import logging
import os
import pytz
from datetime import datetime
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify
from app.config import Config

# --- Application Setup ---

# First, load config to get the timezone
try:
    config = Config()
    logger = logging.getLogger(__name__) # Get logger instance
    logger.info("Configuration loaded successfully.")
except Exception as e:
    # A base logger for critical failure
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.critical(f"FATAL: Could not load configuration. {e}")
    exit(1)

# --- Timezone-Aware Logger ---
class TimezoneFormatter(logging.Formatter):
    """Custom formatter to add timezone to log records."""
    def __init__(self, fmt=None, datefmt=None, style='%'):
        super().__init__(fmt, datefmt, style)

    def formatTime(self, record, datefmt=None):
        """Converts log record time to the configured timezone dynamically."""
        tz_name = config.get('timezone', 'UTC')
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone('UTC')
            
        dt = datetime.fromtimestamp(record.created, tz)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.isoformat()

# --- Configure Logging ---
LOG_FILE = "/logs/domain-manager.log"

log_format = '%(asctime)s - %(levelname)s - %(message)s'
date_format = '%Y-%m-%d %H:%M:%S'

# Create the custom formatter
formatter = TimezoneFormatter(fmt=log_format, datefmt=date_format)

# Create file handler
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1048576, backupCount=5)
file_handler.setFormatter(formatter)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Get the root logger and apply handlers
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [file_handler, console_handler] # Replace default handlers

# --- Create Flask App ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default_insecure_key_CHANGE_ME')

# This is what the @login_required decorator reads.
app.config.update(config.settings)

# --- Import Routes ---
logger.info("Importing web routes...")
from app import routes
# from app import auth  <--- REMOVED THIS LINE

# --- Health Check ---
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

# --- Scheduler Setup ---
logger.info("Importing scheduler...")
from app.scheduler import start_scheduler

if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    start_scheduler()

# --- Main Entrypoint ---
if __name__ == '__main__':
    logger.info("Starting development server on http://localhost:8080")
    app.run(debug=True, host='0.0.0.0', port=8080)
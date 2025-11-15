import logging
import os
from app.app import app
from app.scheduler import run_scheduler

# ---
# This file's ONLY job is to start the scheduler.
# It runs as a separate process from the web server.
# ---

# Set up a basic logger for this worker
log_format = '%(asctime)s - %(levelname)s - %(message)s'
date_format = '%Y-%m-%d %H:%M:%S'
logging.basicConfig(level=logging.INFO, format=log_format, datefmt=date_format)
logger = logging.getLogger(__name__)

# Make sure the log file exists and has the right permissions
LOG_FILE = "/logs/domain-manager.log"
if not os.path.exists(LOG_FILE):
    try:
        open(LOG_FILE, 'a').close()
        os.chmod(LOG_FILE, 0o666)
    except Exception as e:
        logger.warning(f"Could not create log file: {e}")

logger.info("Starting scheduler worker process...")

# Run the main scheduler loop
# We need an app context for the scheduler's config/db access
with app.app_context():
    run_scheduler()
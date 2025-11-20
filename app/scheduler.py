import logging
import schedule
import time
import threading
from datetime import datetime
import pytz
import json
import os
import copy
from dateutil.relativedelta import relativedelta

from app.app import app, config
from app.services import (
    PublicIPService,
    Route53Service,
    CertbotService,
    NotificationService,
    CertificateMonitor
)

logger = logging.getLogger(__name__)

# --- State Management ---
STATE_FILE = "/config/app_state.json"
state_lock = threading.Lock()

# This is the default structure for the app state
app_state = {
    "public_ip": None,
    "last_ip_check_time": None,
    "domain_states": {}
}

# --- DEMO MODE Service Initialization ---

IS_DEMO_MODE = config.get('demo_mode', False)

if IS_DEMO_MODE:
    # In Demo Mode, all external services are "None"
    # This prevents any real AWS/IP/Certbot calls from being made.
    logger.info("Demo Mode: Skipping initialization of external services.")
    ip_service = None
    r53_service = None
    cert_service = None
    cert_monitor = None
else:
    # In Real Mode, initialize all services as normal.
    logger.info("Initializing external services for real mode.")
    ip_service = PublicIPService()
    r53_service = Route53Service()
    cert_service = CertbotService()
    cert_monitor = CertificateMonitor()

# NotificationService is initialized in both modes
# so the "Send Test Notification" button can work.
notify_service = NotificationService()

# --- State Persistence ---

def load_state():
    """Loads the app_state from a JSON file on startup."""
    global app_state
    
    # --- Skip loading state in demo mode ---
    if IS_DEMO_MODE:
        logger.info("Demo Mode: Skipping state load.")
        return
    
    with state_lock:
        if not os.path.exists(STATE_FILE):
            logger.info(f"State file not found at {STATE_FILE}. Starting with fresh state.")
            return

        try:
            with open(STATE_FILE, 'r') as f:
                loaded_state = json.load(f)
                
            # Convert ALL string timestamps back to datetime objects
            if loaded_state.get("last_ip_check_time"):
                loaded_state["last_ip_check_time"] = datetime.fromisoformat(loaded_state["last_ip_check_time"])
            
            for domain, state in loaded_state.get("domain_states", {}).items():
                if state.get("ssl_expiration"):
                    state["ssl_expiration"] = datetime.fromisoformat(state["ssl_expiration"])
                if state.get("last_update_time"):
                    state["last_update_time"] = datetime.fromisoformat(state["last_update_time"])
                if state.get("ssl_last_renew"):
                    state["ssl_last_renew"] = datetime.fromisoformat(state["ssl_last_renew"])
            
            app_state.update(loaded_state)
            logger.info("Successfully loaded previous state from disk.")
                
        except Exception as e:
            logger.error(f"Error loading state file: {e}. Starting with fresh state.")
            app_state.update({
                "public_ip": None,
                "last_ip_check_time": None,
                "domain_states": {}
            })

def save_state():
    """Saves the current app_state to a JSON file."""
    global app_state
    
    # --- Skip saving state in demo mode ---
    if IS_DEMO_MODE:
        return
    
    with state_lock:
        try:
            state_to_save = copy.deepcopy(app_state)

            if isinstance(state_to_save.get("last_ip_check_time"), datetime):
                state_to_save["last_ip_check_time"] = state_to_save["last_ip_check_time"].isoformat()

            for domain, state in state_to_save["domain_states"].items():
                if isinstance(state.get("ssl_expiration"), datetime):
                    state["ssl_expiration"] = state["ssl_expiration"].isoformat()
                if isinstance(state.get("last_update_time"), datetime):
                    state["last_update_time"] = state["last_update_time"].isoformat()
                if isinstance(state.get("ssl_last_renew"), datetime):
                    state["ssl_last_renew"] = state["ssl_last_renew"].isoformat()
            
            with open(STATE_FILE, 'w') as f:
                json.dump(state_to_save, f, indent=2)
            logger.info("Successfully saved app state to disk.")
        except Exception as e:
            logger.error(f"Error saving state file: {e}")

# --- Helper Function ---
def get_user_timezone():
    """Gets the pytz timezone object from config."""
    try:
        tz_name = config.get('timezone', 'UTC')
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{tz_name}'. Defaulting to UTC.")
        return pytz.timezone('UTC')

def get_current_time_in_tz():
    """Returns a timezone-aware datetime object for 'now'."""
    tz = get_user_timezone()
    return datetime.now(tz)

def get_utc_time_for_local_string(time_str):
    """Converts a local time string (e.g., '02:30') to a UTC string."""
    tz = get_user_timezone()
    now_in_tz = datetime.now(tz)
    
    target_time = datetime.strptime(time_str, '%H:%M').time()
    target_dt_local = now_in_tz.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)

    target_dt_utc = target_dt_local.astimezone(pytz.utc)
    
    return target_dt_utc.strftime('%H:%M')

# --- Core Job Functions ---

def run_ddns_update():
    """
    Main DDNS update job.
    """
    with app.app_context(): 
        logger.info("Scheduler: Running DDNS update check...")
        
        global_notifications_enabled = config.get('notifications', {}).get('enabled', False)
        
        new_public_ip = ip_service.get_public_ip()
        app_state["last_ip_check_time"] = get_current_time_in_tz()
        
        if not new_public_ip:
            logger.error("DDNS Update SKIPPED: Could not determine public IP.")
            if app_state.get("public_ip") is not None and global_notifications_enabled: 
                notify_service.send_notification(
                    "DDNS IP Check FAILED",
                    "Failed to retrieve the container's public IP address. All IP providers failed."
                )
            app_state["public_ip"] = None
            save_state()
            return

        ip_has_changed = (app_state.get("public_ip") != new_public_ip)
        if ip_has_changed:
            logger.info(f"Public IP has changed! New IP: {new_public_ip} (Old: {app_state.get('public_ip')})")
            app_state["public_ip"] = new_public_ip
        else:
            logger.info(f"Public IP ({new_public_ip}) has not changed.")

        for domain_config in config.get_domains():
            domain_name = domain_config['name']
            
            if domain_name not in app_state['domain_states']:
                app_state['domain_states'][domain_name] = {}
            
            if not domain_config.get('ddns', False):
                continue
                
            recorded_ip = r53_service.get_a_record_ip(domain_name)
            app_state['domain_states'][domain_name]['recorded_ip'] = recorded_ip
            
            app_state['domain_states'][domain_name]['last_update_time'] = get_current_time_in_tz()
            
            auto_update_enabled = domain_config.get('auto_update', True) 
            domain_notifications_enabled = domain_config.get('notifications', True) 
            send_alerts = global_notifications_enabled and domain_notifications_enabled

            if recorded_ip and recorded_ip.startswith("ALIAS:"):
                logger.warning(f"[{domain_name}] Skipping update, domain is an ALIAS record.")
                continue

            if new_public_ip != recorded_ip:
                logger.info(f"[{domain_name}] IP mismatch. Recorded: {recorded_ip}, Public: {new_public_ip}.")
                
                if auto_update_enabled:
                    logger.info(f"[{domain_name}] Auto-update enabled. Updating...")
                    success = r53_service.update_a_record_ip(domain_name, new_public_ip)
                    
                    if success:
                        logger.info(f"[{domain_name}] Successfully updated to {new_public_ip}")
                        app_state['domain_states'][domain_name]['recorded_ip'] = new_public_ip
                        
                        if send_alerts:
                            notify_service.send_notification(
                                f"DDNS IP Updated for {domain_name}",
                                f"The IP address for {domain_name} has been successfully updated.\n\n"
                                f"New IP: {new_public_ip}\n"
                                f"Old IP: {recorded_ip or 'N/A'}"
                            )
                    else:
                        logger.error(f"[{domain_name}] Failed to update in Route 53.")
                        if send_alerts:
                            notify_service.send_notification(
                                f"DDNS IP Update FAILED for {domain_name}",
                                f"The IP address update for {domain_name} failed. "
                                f"Please check the application logs and IAM permissions."
                            )
                else:
                    logger.info(f"[{domain_name}] Auto-update is disabled. IP was not updated.")
                    if send_alerts:
                         notify_service.send_notification(
                            f"DDNS IP Mismatch DETECTED for {domain_name}",
                            f"An IP mismatch was detected for {domain_name}, but auto-update is disabled.\n\n"
                            f"Please update the IP manually.\n\n"
                            f"Public IP: {new_public_ip}\n"
                            f"Recorded IP: {recorded_ip or 'N/A'}"
                        )
            else:
                logger.info(f"[{domain_name}] IPs match ({new_public_ip}). No update needed.")
        
        save_state()

def _run_ssl_check_thread():
    """
    The actual worker function that runs in a background thread.
    It includes sleeps to prevent rate limiting.
    """
    with app.app_context():
        logger.info("Scheduler: Background SSL thread started.")
        global_notifications_enabled = config.get('notifications', {}).get('enabled', False)
        
        domains = config.get_domains()
        total_domains = len(domains)
        
        for i, domain_config in enumerate(domains):
            # Check if SSL is enabled for this domain
            if not domain_config.get('ssl', {}).get('enabled'):
                continue

            domain_name = domain_config['name']
            
            auto_update_enabled = domain_config.get('auto_update', True)
            domain_notifications_enabled = domain_config.get('notifications', True)
            send_alerts = global_notifications_enabled and domain_notifications_enabled
            
            # --- 1. The Check Logic ---
            if not cert_monitor.get_cert_expiration_date(domain_name):
                logger.info(f"[{domain_name}] Skipping renewal check, certificate is missing.")
            else:
                logger.info(f"[{domain_name}] Checking for SSL renewal (Auto-update: {auto_update_enabled})...")
                success, output = cert_service.run_renewal_check(domain_name, auto_update_enabled)
            
                if not success:
                    logger.error(f"[{domain_name}] Certbot renewal check FAILED. Output: {output}")
                    if send_alerts:
                        notify_service.send_notification(
                            f"SSL Certificate Renewal FAILED for {domain_name}",
                            f"The daily 'certbot renew' command failed. See logs for details.\n\nOutput:\n{output}"
                        )
                else:
                    logger.info(f"[{domain_name}] Certbot renewal check completed. Output: {output}")
                    if "Congratulations, all renewals succeeded" in output or "Renewed" in output:
                        app_state['domain_states'][domain_name]['ssl_last_renew'] = get_current_time_in_tz()
                        if send_alerts:
                            notify_service.send_notification(
                                "SSL Certificate Renewed Successfully",
                                f"SSL certificate for {domain_name} was successfully renewed.\n\nOutput:\n{output}"
                            )
                
                logger.info(f"[{domain_name}] Re-checking SSL expiration date after renewal.")
                expiry_date = cert_monitor.get_cert_expiration_date(domain_name)
                if domain_name in app_state['domain_states']:
                    app_state['domain_states'][domain_name]['ssl_expiration'] = expiry_date
            
            save_state()
            
            # --- 2. The "Nap" Logic ---
            # Do not sleep after the very last domain
            if i < total_domains - 1:
                processed_count = i + 1
                
                # Every 10 domains, sleep 3 hours
                if processed_count % 10 == 0:
                    logger.info(f"SSL Batch: Processed {processed_count} domains. Sleeping 3 hours to respect rate limits...")
                    time.sleep(10800) # 3 hours
                else:
                    # Otherwise, sleep 10 minutes
                    logger.info(f"SSL Batch: Processed {domain_name}. Sleeping 10 minutes before next domain...")
                    time.sleep(600) # 10 minutes

        logger.info("Scheduler: Background SSL checks completed for all domains.")

def run_ssl_check():
    """
    Triggers the SSL check in a separate thread so it doesn't block the scheduler.
    """
    logger.info("Scheduler: Triggering threaded SSL renewal checks...")
    
    # Check if a thread is already running to prevent double-stacking
    for thread in threading.enumerate():
        if thread.name == "SSL_Worker_Thread":
            logger.warning("SSL Check triggered, but a previous SSL thread is still running. Skipping.")
            return

    t = threading.Thread(target=_run_ssl_check_thread, name="SSL_Worker_Thread", daemon=True)
    t.start()

def run_log_cleanup():
    """
    Deletes Certbot logs AND System logs older than the retention period.
    """
    try:
        retention_str = config.get('log_retention', '3 months')
        logger.info(f"Scheduler: Running log cleanup with retention '{retention_str}'...")
        
        # Parse retention string
        parts = retention_str.split()
        if len(parts) != 2:
            parts = ['3', 'months']
        
        try:
            value = int(parts[0])
        except ValueError:
            value = 3
            
        unit = parts[1].lower()
        delta_kwargs = {}
        if "day" in unit: delta_kwargs['days'] = value
        elif "week" in unit: delta_kwargs['weeks'] = value
        elif "month" in unit: delta_kwargs['months'] = value
        elif "year" in unit: delta_kwargs['years'] = value
        else: delta_kwargs['months'] = 3
        
        cutoff_date = get_current_time_in_tz() - relativedelta(**delta_kwargs)
        
        # --- 1. Clean Certbot Logs ---
        certs_dir = "/certs"
        deleted_count = 0
        user_tz = get_user_timezone()

        for domain_config in config.get_domains():
            domain_name = domain_config['name']
            domain_cert_dir = os.path.join(certs_dir, domain_name)
            if os.path.isdir(domain_cert_dir):
                for filename in os.listdir(domain_cert_dir):
                    if filename.startswith("letsencrypt.log"):
                        file_path = os.path.join(domain_cert_dir, filename)
                        try:
                            if datetime.fromtimestamp(os.path.getmtime(file_path), user_tz) < cutoff_date:
                                os.remove(file_path)
                                deleted_count += 1
                        except Exception:
                            pass
        
        # --- 2. Clean System Logs (domain-manager.log.1, .2, etc) ---
        log_dir = "/logs"
        if os.path.isdir(log_dir):
            for filename in os.listdir(log_dir):
                if filename.startswith("domain-manager.log."): # Matches rotated files
                    file_path = os.path.join(log_dir, filename)
                    try:
                        if datetime.fromtimestamp(os.path.getmtime(file_path), user_tz) < cutoff_date:
                            logger.info(f"Deleting old system log: {filename}")
                            os.remove(file_path)
                            deleted_count += 1
                    except Exception:
                        pass

        logger.info(f"Log cleanup complete. Deleted {deleted_count} file(s).")

    except Exception as e:
        logger.error(f"Error during log cleanup: {e}")

def run_initial_setup():
    """
    Runs once on startup to populate state.
    """
    with app.app_context():
        load_state()
        
        # --- Skip in demo mode ---
        if IS_DEMO_MODE:
            logger.info("Demo Mode: Skipping initial setup.")
            return
        
        logger.info("Running initial setup... checking for missing SSL certs.")
        for domain_config in config.get_domains():
            if domain_config.get('ssl', {}).get('enabled'):
                domain_name = domain_config['name']
                
                existing_ssl_data = app_state.get("domain_states", {}).get(domain_name, {}).get("ssl_expiration")
                
                if not existing_ssl_data:
                    expiry_date = cert_monitor.get_cert_expiration_date(domain_name)
                    if domain_name not in app_state["domain_states"]:
                        app_state["domain_states"][domain_name] = {}
                    app_state['domain_states'][domain_name]['ssl_expiration'] = expiry_date
                    
                    if expiry_date:
                        logger.info(f"[{domain_name}] Found existing certificate. Expires: {expiry_date.strftime('%Y-%m-%d')}")
                    else:
                        logger.warning(f"[{domain_name}] Certificate not found. A user must create it manually.")
                
        logger.info("Initial setup complete.")
        save_state()

# --- Scheduler Thread ---

def run_scheduler():
    """Runs the main scheduler loop in a separate thread."""
    
    if IS_DEMO_MODE:
        logger.info("Demo Mode: Scheduler is disabled.")
        return 
    
    # --- Schedule jobs ---
    
    # 1. SSL Check
    ssl_utc_time = get_utc_time_for_local_string("02:30")
    schedule.every().day.at(ssl_utc_time).do(run_ssl_check)
    
    # --- DEBUG LOG: Print exactly when we think we are running ---
    tz = get_user_timezone()
    logger.info(f"Scheduler: SSL Check scheduled for {ssl_utc_time} UTC. (Target was 02:30 {tz})")
    
    # 2. Log Cleanup
    log_utc_time = get_utc_time_for_local_string("03:30")
    schedule.every().day.at(log_utc_time).do(run_log_cleanup)
    
    interval_str = config.get('ip_check_interval', '5m')
    log_msg = ""
    run_first_check = True
    
    if interval_str == '5m':
        for minute in range(0, 60, 5):
            schedule.every().hour.at(f":{minute:02d}").do(run_ddns_update)
        log_msg = "every 5 minutes (at :00, :05...)"
    elif interval_str == '10m':
        for minute in range(0, 60, 10):
            schedule.every().hour.at(f":{minute:02d}").do(run_ddns_update)
        log_msg = "every 10 minutes (at :00, :10...)"
    elif interval_str == '60m':
        schedule.every().hour.at(":00").do(run_ddns_update)
        log_msg = "every hour (at :00)"
    elif interval_str == '24h':
        ip_utc_time = get_utc_time_for_local_string("00:00")
        schedule.every().day.at(ip_utc_time).do(run_ddns_update)
        log_msg = f"daily at 00:00 local (schedules for {ip_utc_time} UTC)"
    elif interval_str == 'disabled':
        log_msg = "disabled"
        run_first_check = False
    else:
        logger.warning(f"Invalid 'ip_check_interval' value: '{interval_str}'. Defaulting to 5 minutes.")
        for minute in range(0, 60, 5):
            schedule.every().hour.at(f":{minute:02d}").do(run_ddns_update)
        log_msg = "every 5 minutes (defaulted)"

    logger.info(f"Scheduler jobs registered. DDNS check: {log_msg}. SSL check at 02:30 local ({ssl_utc_time} UTC).")
    
    # Now run the slow initial setup
    run_initial_setup()
    
    if run_first_check:
        logger.info("Running initial DDNS check...")
        run_ddns_update() 
    
    while True:
        schedule.run_pending()
        time.sleep(1)

def start_scheduler():
    """Starts the scheduler in a non-blocking daemon thread."""
    
    # --- Skip in demo mode ---
    if IS_DEMO_MODE:
        logger.info("Demo Mode: Skipping scheduler thread start.")
        return # Do not start the thread

    logger.info("Starting background scheduler thread...")
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
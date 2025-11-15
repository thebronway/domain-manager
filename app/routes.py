import logging
import os
import schedule
import pytz
from datetime import datetime
from collections import deque

from flask import render_template, jsonify, flash, redirect, url_for, Response
from app.app import app, config
from app.scheduler import (
    app_state, 
    run_ddns_update, 
    run_ssl_check, 
    cert_service,
    cert_monitor,
    save_state,
    # load_state, <-- REMOVED: Routes should not reload state
    get_user_timezone,
    notify_service,
    r53_service,
    get_current_time_in_tz
)

logger = logging.getLogger(__name__)
LOG_FILE = "/logs/domain-manager.log"

# --- Helper ---
def get_next_run_time(job_func_name):
    """
    Finds the EARLIEST next run time for a scheduled job by its function name.
    """
    tz = get_user_timezone()
    next_runs = []

    try:
        # Check ALL jobs
        for job in schedule.jobs.copy():
            if job.job_func.__name__ == job_func_name:
                if job.next_run:
                    # Store the next_run time
                    next_runs.append(job.next_run)
        
        if not next_runs:
            return "Not scheduled"

        # Find the earliest time in the list
        next_run_utc = min(next_runs)
        
        # Convert to user timezone
        utc_time = pytz.utc.localize(next_run_utc)
        local_time = utc_time.astimezone(tz)
        
        return local_time.strftime("%Y-%m-%d %H:%M:%S %Z")

    except Exception as e:
        logger.error(f"Error getting next run time for {job_func_name}: {e}")
        return "Error"

# --- Main Dashboard Route ---

@app.route('/')
def index():
    """
    Renders the main dashboard page.
    """
    try:
        # load_state() <-- REMOVED: This was causing the UI not to update
        
        domain_configs = config.get_domains()
        
        next_ddns_run = get_next_run_time("run_ddns_update")
        next_ssl_run = get_next_run_time("run_ssl_check")
        
        return render_template('index.html', 
                               app_state=app_state, 
                               domain_configs=domain_configs,
                               next_ddns_run=next_ddns_run,
                               next_ssl_run=next_ssl_run)
    except Exception as e:
        logger.error(f"Error rendering dashboard: {e}")
        flash(f"An error occurred while loading the dashboard: {e}", "danger")
        
        # --- THIS IS THE FIX ---
        # Pass a default app_state structure so the template doesn't crash
        default_state = {
            "public_ip": "Error",
            "last_ip_check_time": None,
            "domain_states": {}
        }
        return render_template('index.html', app_state=default_state, domain_configs=[], next_ddns_run="Error", next_ssl_run="Error")

# --- API/Manual Trigger Routes ---

@app.route('/api/trigger/ddns', methods=['POST'])
def trigger_ddns():
    """
    Manually triggers the global DDNS update check.
    This will respect the auto_update flag for each domain.
    """
    logger.info("Manual global DDNS update triggered by user.")
    try:
        run_ddns_update() 
        flash("Manual DDNS update check initiated.", "info")
    except Exception as e:
        logger.error(f"Error during manual DDNS trigger: {e}")
        flash(f"An error occurred: {e}", "danger")
    
    return redirect(url_for('index'))

@app.route('/api/trigger/ssl_renew', methods=['POST'])
def trigger_ssl():
    """
    Manually triggers the global SSL renewal check.
    This will respect the auto_update flag for each domain.
    """
    logger.info("Manual global SSL renewal triggered by user.")
    try:
        run_ssl_check()
        flash("Manual SSL renewal check initiated.", "info")
    except Exception as e:
        logger.error(f"Error during manual SSL trigger: {e}")
        flash(f"An error occurred: {e}", "danger")
    
    return redirect(url_for('index'))

@app.route('/api/trigger/ssl_create/<domain_name>', methods=['POST'])
def trigger_create_cert(domain_name):
    """
    Manually triggers a NEW SSL certificate creation (bypasses auto_update).
    """
    logger.info(f"[{domain_name}] Manual SSL creation triggered by user.")
    
    domain_config = next((d for d in config.get_domains() if d['name'] == domain_name), None)
    
    if not domain_config:
        logger.error(f"[{domain_name}] Manual create failed. Domain not found in config.")
        flash(f"Could not find config for {domain_name}", "danger")
        return redirect(url_for('index'))
    
    # --- FIX: Add notification flag checks ---
    global_notifications_enabled = config.get('notifications', {}).get('enabled', False)
    domain_notifications_enabled = domain_config.get('notifications', True) 
    send_alerts = global_notifications_enabled and domain_notifications_enabled

    try:
        is_wildcard = domain_config.get('ssl', {}).get('wildcard', False)
        
        success, output = cert_service.create_certificate(domain_name, is_wildcard)
        
        if not success:
            logger.error(f"[{domain_name}] Certbot command failed: {output}")
            flash(f"Failed to create certificate for {domain_name}: {output}", "danger")
            # --- FIX: Send notification on failure ---
            if send_alerts:
                notify_service.send_notification(
                    f"SSL Certificate Creation FAILED for {domain_name}",
                    f"A manual attempt to create an SSL certificate failed.\n\nError:\n{output}"
                )
            return redirect(url_for('index'))

        logger.info(f"[{domain_name}] Certbot command ran. Re-checking for cert file...")
        new_expiry_date = cert_monitor.get_cert_expiration_date(domain_name)
        
        if new_expiry_date:
            logger.info(f"[{domain_name}] New cert found! Expires: {new_expiry_date}")
            app_state['domain_states'][domain_name]['ssl_expiration'] = new_expiry_date
            flash(f"Successfully created and verified certificate for {domain_name}.", "success")
            # --- FIX: Send notification on success ---
            if send_alerts:
                notify_service.send_notification(
                    f"SSL Certificate Created for {domain_name}",
                    f"A new SSL certificate was successfully created for {domain_name}.\n\n"
                    f"It expires on: {new_expiry_date.strftime('%Y-%m-%d')}"
                )
        else:
            logger.error(f"[{domain_name}] Certbot command Succeeded, but cert file is still not found!")
            flash(f"Certbot command ran, but the new cert could not be found. Check logs for {domain_name}.", "warning")
            # --- FIX: Send notification on partial failure ---
            if send_alerts:
                notify_service.send_notification(
                    f"SSL Certificate Creation WARNING for {domain_name}",
                    f"The Certbot command reported success, but the application could not find the new certificate file. "
                    f"Please check the logs for {domain_name}."
                )
            
    except Exception as e:
        logger.error(f"[{domain_name}] Error during manual SSL creation: {e}")
        flash(f"An error occurred: {e}", "danger")
    
    save_state() # Save changes to state
    return redirect(url_for('index'))

@app.route('/api/trigger/test_notification', methods=['POST'])
def trigger_test_notification():
    """
    Sends a test email notification.
    """
    logger.info("Manual test notification triggered by user.")
    try:
        success, message = notify_service.send_test_notification()
        if success:
            flash(f"Test notification sent: {message}", "success")
        else:
            flash(f"Test notification FAILED: {message}", "danger")
    except Exception as e:
        logger.error(f"Error during test notification trigger: {e}")
        flash(f"An error occurred: {e}", "danger")
    
    return redirect(url_for('index'))

@app.route('/api/refresh_ip/<domain_name>', methods=['GET'])
def trigger_refresh_ip(domain_name):
    """
    Refreshes just the 'Recorded IP' for a single domain.
    Does not perform an update.
    """
    logger.info(f"[{domain_name}] Manual refresh of recorded IP triggered.")
    try:
        # load_state() <-- REMOVED: This was causing the UI not to update
        ip = r53_service.get_a_record_ip(domain_name)
        
        if domain_name not in app_state['domain_states']:
             app_state['domain_states'][domain_name] = {}
             
        app_state['domain_states'][domain_name]['recorded_ip'] = ip
        save_state()
        flash(f"Refreshed Recorded IP for {domain_name}. New value: {ip or 'N/A'}", "info")
    except Exception as e:
        logger.error(f"Error during IP refresh: {e}")
        flash(f"An error occurred refreshing IP: {e}", "danger")

    return redirect(url_for('index'))

@app.route('/api/force_update_ip/<domain_name>', methods=['POST'])
def trigger_force_update_ip(domain_name):
    """
    Forces an update of a single domain's IP, bypassing auto_update checks.
    This fixes the "manual timestamp" bug.
    """
    logger.info(f"[{domain_name}] Manual FORCE update triggered by user.")
    try:
        # load_state() <-- REMOVED: This was causing the UI not to update
        
        # Find the domain config
        domain_config = next((d for d in config.get_domains() if d['name'] == domain_name), None)
        if not (domain_config and domain_config.get('ddns', False)):
             flash(f"Cannot update IP: {domain_name} does not have DDNS enabled.", "danger")
             return redirect(url_for('index'))

        # --- FIX: Add notification flag checks ---
        global_notifications_enabled = config.get('notifications', {}).get('enabled', False)
        domain_notifications_enabled = domain_config.get('notifications', True) 
        send_alerts = global_notifications_enabled and domain_notifications_enabled

        # --- FIX: Use dictionary .get() access for app_state ---
        public_ip = app_state.get("public_ip")
        
        # --- FIX: Get old_ip for notification ---
        old_ip = app_state.get("domain_states", {}).get(domain_name, {}).get("recorded_ip", "N/A")
        
        if not public_ip:
            flash("Cannot update IP: Public IP is unknown.", "danger")
            return redirect(url_for('index'))

        logger.info(f"[{domain_name}] Forcing update to {public_ip}...")
        success = r53_service.update_a_record_ip(domain_name, public_ip)
        
        if success:
            app_state['domain_states'][domain_name]['recorded_ip'] = public_ip
            app_state['domain_states'][domain_name]['last_update_time'] = get_current_time_in_tz()
            save_state()
            flash(f"Successfully forced update for {domain_name}.", "success")
            # --- FIX: Send notification on success ---
            if send_alerts:
                notify_service.send_notification(
                    f"DDNS IP Manually Updated for {domain_name}",
                    f"The IP address for {domain_name} has been manually updated.\n\n"
                    f"New IP: {public_ip}\n"
                    f"Old IP: {old_ip}"
                )
        else:
            flash(f"Failed to force update for {domain_name}. Check logs.", "danger")
            # --- FIX: Send notification on failure ---
            if send_alerts:
                notify_service.send_notification(
                    f"DDNS IP Manual Update FAILED for {domain_name}",
                    f"A manual IP address update for {domain_name} failed. "
                    f"Please check the application logs and IAM permissions."
                )
            
    except Exception as e:
        logger.error(f"Error during force IP update: {e}")
        flash(f"An error occurred: {e}", "danger")

    return redirect(url_for('index'))

@app.route('/logs/<domain_name>')
def view_log(domain_name):
    """
    Renders a page to view logs for a specific domain.
    """
    log_content = ""
    filter_key = f"[{domain_name}]"
    
    try:
        # --- FIX: Use deque for memory-efficient log reading ---
        filtered_lines = deque(maxlen=1000)
        with open(LOG_FILE, 'r') as f:
            for line in f:
                if filter_key in line:
                    filtered_lines.append(line)
        
        # Reverse the deque to show newest first
        log_content = "".join(list(filtered_lines)[::-1])
        
        if not log_content:
            log_content = f"No log entries found for '{domain_name}'.\n(Note: General app logs are not shown here.)"
            
        return render_template('view_log.html', log_content=log_content, domain_name=domain_name)
    except FileNotFoundError:
        logger.error(f"Log file not found at {LOG_FILE}")
        flash(f"Log file not found. Has the container just started?", "warning")
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        flash(f"An error occurred while reading the log file: {e}", "danger")
        return redirect(url_for('index'))
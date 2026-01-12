import logging
import os
import schedule
import pytz
import random
import glob
from datetime import datetime, timedelta
from collections import deque

from flask import render_template, jsonify, flash, redirect, url_for, Response, request, send_file
from app.app import app, config
from app.scheduler import (
    app_state, 
    cert_service,
    cert_monitor,
    r53_service,
    run_ddns_update, 
    run_ssl_check, 
    save_state,
    get_user_timezone,
    notify_service,
    get_current_time_in_tz
)

logger = logging.getLogger(__name__)
LOG_FILE = "/logs/domain-manager.log"

# --- Helper ---
def get_next_run_time(job_func_name):
    """
    Finds the EARLIEST next run time for a scheduled job by its function name.
    """
    if config.demo_mode:
        if job_func_name == "run_ddns_update":
            return "Every 5 Mins (Demo)"
        if job_func_name == "run_ssl_check":
            return "02:30 Daily (Demo)"
        return "Scheduled (Demo)"

    tz = get_user_timezone()
    next_runs = []

    try:
        for job in schedule.jobs.copy():
            if job.job_func.__name__ == job_func_name:
                if job.next_run:
                    next_runs.append(job.next_run)
        
        if not next_runs:
            return "Not scheduled"

        next_run_utc = min(next_runs)
        utc_time = pytz.utc.localize(next_run_utc)
        local_time = utc_time.astimezone(tz)
        
        return local_time.strftime("%Y-%m-%d %H:%M:%S %Z")

    except Exception as e:
        logger.error(f"Error getting next run time for {job_func_name}: {e}")
        return "Error"

def _generate_fake_state(domain_configs):
    """
    Builds a fake app_state dict based on the ACTUAL current domain_configs.
    Randomizes statuses with a positive bias (mostly green).
    """
    # Generate a random "Current Public IP"
    fake_public_ip = f"{random.randint(11, 199)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    
    tz = get_user_timezone()
    now = datetime.now(tz)
    
    fake_state = {
        "public_ip": fake_public_ip, 
        "last_ip_check_time": now,
        "domain_states": {},
        "provider_error": None # No config errors in demo mode
    }

    for d in domain_configs:
        domain_name = d['name']
        ddns_enabled = d.get('ddns', False)
        ssl_enabled = d.get('ssl', {}).get('enabled', False)
        
        # 1. Generate Recorded IP (DDNS)
        recorded_ip = None
        if ddns_enabled:
            # 90% chance to match, 10% chance to be different
            if random.random() > 0.1:
                recorded_ip = fake_public_ip
            else:
                recorded_ip = f"{random.randint(11, 199)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
        
        # 2. Generate SSL Expiration
        ssl_exp = None
        if ssl_enabled:
            # 90% chance to be valid (future), 10% chance to be expired/missing
            if random.random() > 0.1:
                # Valid: Expires in 30 to 90 days
                days_future = random.randint(30, 90)
                ssl_exp = now + timedelta(days=days_future)
            else:
                # Issue: Expires in -5 days (expired) or None (missing)
                if random.choice([True, False]):
                    ssl_exp = now - timedelta(days=random.randint(1, 10)) # Expired
                else:
                    ssl_exp = None # Missing

        fake_state['domain_states'][domain_name] = {
            'recorded_ip': recorded_ip,
            'ssl_expiration': ssl_exp,
            'last_update_time': now - timedelta(minutes=random.randint(5, 60)),
            'ssl_last_renew': now - timedelta(days=random.randint(1, 60))
        }
        
    return fake_state

def _parse_log_lines(lines):
    """
    Parses log lines to add CSS classes for syntax highlighting.
    """
    parsed_lines = []
    for line in lines:
        clean_line = line.strip()
        if not clean_line: 
            continue
            
        css_class = ""
        lower_line = clean_line.lower()
        
        if "error" in lower_line or "critical" in lower_line or "failed" in lower_line:
            css_class = "log-error"
        elif "warning" in lower_line:
            css_class = "log-warning"
        elif "success" in lower_line or "match" in lower_line or "updated to" in lower_line:
            css_class = "log-success"
            
        parsed_lines.append({
            "text": clean_line,
            "class": css_class
        })
    return parsed_lines

# --- Main Dashboard Route ---

@app.route('/')
def index():
    try:
        domain_configs = config.get_domains()
        
        # Check global SSL setting
        ssl_globally_enabled = config.get('cert_management', {}).get('enabled', True)
        
        if config.demo_mode:
            dashboard_state = _generate_fake_state(domain_configs)
            next_ddns_run = "Every 5 Mins (Demo)"
            next_ssl_run = "02:30 Daily (Demo)"
        else:
            dashboard_state = app_state
            next_ddns_run = get_next_run_time("run_ddns_update")
            next_ssl_run = get_next_run_time("run_ssl_check")

        summary = {
            "ip_total_enabled": 0, "ip_synced": 0, "ip_mismatch": 0,
            "ssl_total_enabled": 0, "ssl_valid": 0, "ssl_expiring": 0,
            "next_cert_domain": None, "next_cert_date": None
        }
        
        tz = get_user_timezone()
        now_aware = datetime.now(tz)
        expiration_threshold = now_aware + timedelta(days=30)
        earliest_expiry = None

        for d in domain_configs:
            d_name = d['name']
            d_state = dashboard_state.get('domain_states', {}).get(d_name, {})
            
            if d.get('ddns', False):
                summary['ip_total_enabled'] += 1
                pub = dashboard_state.get('public_ip')
                rec = d_state.get('recorded_ip')
                if pub and rec and pub == rec:
                    summary['ip_synced'] += 1
                elif rec and not rec.startswith('ALIAS'):
                     summary['ip_mismatch'] += 1
            
            # Use global toggle AND domain toggle
            if ssl_globally_enabled and d.get('ssl', {}).get('enabled', False):
                summary['ssl_total_enabled'] += 1
                exp = d_state.get('ssl_expiration')
                if exp:
                    if exp.tzinfo is None:
                        exp = pytz.utc.localize(exp)
                    if earliest_expiry is None or exp < earliest_expiry:
                        earliest_expiry = exp
                        summary['next_cert_domain'] = d_name
                        summary['next_cert_date'] = exp
                    if exp < expiration_threshold:
                        summary['ssl_expiring'] += 1
                    else:
                        summary['ssl_valid'] += 1
                else:
                    summary['ssl_expiring'] += 1

        return render_template('index.html', 
                               app_state=dashboard_state, 
                               domain_configs=domain_configs,
                               next_ddns_run=next_ddns_run,
                               next_ssl_run=next_ssl_run,
                               summary=summary,
                               demo_mode=config.demo_mode,
                               ssl_enabled=ssl_globally_enabled) # <-- This is the key addition
    except Exception as e:
        logger.error(f"Error rendering dashboard: {e}")
        flash(f"An error occurred while loading the dashboard: {e}", "danger")
        return render_template('index.html', 
                                app_state={"public_ip": "Error", "domain_states": {}}, 
                                domain_configs=[], 
                                next_ddns_run="Error", next_ssl_run="Error",
                                summary={}, demo_mode=config.demo_mode,
                                ssl_enabled=True)

# --- Settings Routes ---

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        try:
            new_settings = request.json
            if not new_settings:
                return jsonify({"status": "error", "message": "No data received"}), 400
            
            success = config.save(new_settings)
            
            if success:
                # Reload the scheduler in this process (Web)
                from app.scheduler import reload_scheduler
                reload_scheduler()
                
                flash("Settings saved successfully.", "success")
                return jsonify({"status": "success", "message": "Settings saved."})
            else:
                return jsonify({"status": "error", "message": "Failed to save settings to disk."}), 500
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    return render_template('settings.html', 
                           settings=config.settings, 
                           demo_mode=config.demo_mode,
                           provider=config.provider,
                           provider_error=app_state.get('provider_error'))

# --- Log Routes ---

@app.route('/logs/all')
def view_all_logs():
    """Shows full system logs, including rotated backups."""
    
    if config.demo_mode:
        fake_logs = [
            f"2025-11-19 10:00:00 - INFO - System started in DEMO MODE.",
            f"2025-11-19 10:05:00 - INFO - Checking public IP...",
            f"2025-11-19 10:05:01 - INFO - Public IP matched. No update needed."
        ]
        return render_template('view_log.html', 
                             log_lines=_parse_log_lines(fake_logs), 
                             title="System Logs (Demo)", 
                             download_endpoint="download_main_log")

    try:
        lines = deque(maxlen=20000)
        log_files = glob.glob(f"{LOG_FILE}*")
        
        def get_file_sort_key(filename):
            if filename == LOG_FILE: return 0
            parts = filename.split('.')
            if parts[-1].isdigit(): return int(parts[-1])
            return 999

        log_files.sort(key=get_file_sort_key, reverse=True)

        for lf in log_files:
            if os.path.exists(lf):
                try:
                    with open(lf, 'r') as f:
                        for line in f:
                            lines.append(line)
                except Exception as read_err:
                    logger.warning(f"Could not read rotated log {lf}: {read_err}")
        
        lines_list = list(lines)[::-1]
        
        return render_template('view_log.html', 
                             log_lines=_parse_log_lines(lines_list), 
                             title="System Logs - domainCtrl",
                             download_endpoint="download_main_log")
    except Exception as e:
        flash(f"Error reading logs: {e}", "danger")
        return redirect(url_for('index'))

@app.route('/logs/<domain_name>')
def view_log(domain_name):
    """Renders logs for a specific domain, scanning ALL files."""
    if config.demo_mode:
         return render_template('view_log.html', log_lines=[], title=f"Demo Logs: {domain_name}", download_endpoint="download_main_log")

    filter_key = f"[{domain_name}]"
    matches = []
    
    try:
        log_files = glob.glob(f"{LOG_FILE}*")
        
        def get_file_sort_key(filename):
            if filename == LOG_FILE: return 0
            parts = filename.split('.')
            if parts[-1].isdigit(): return int(parts[-1])
            return 999

        log_files.sort(key=get_file_sort_key, reverse=True)

        for lf in log_files:
            if os.path.exists(lf):
                try:
                    with open(lf, 'r') as f:
                        for line in f:
                            if filter_key in line:
                                matches.append(line)
                except Exception as read_err:
                    logger.warning(f"Could not read rotated log {lf}: {read_err}")
        
        last_1000 = matches[-1000:]
        lines_list = last_1000[::-1]
        
        if not lines_list:
            lines_list = [f"No specific log entries found containing '{filter_key}' in any log file."]
            
        return render_template('view_log.html', 
                             log_lines=_parse_log_lines(lines_list), 
                             title=f"Logs: {domain_name} - domainCtrl",
                             download_endpoint="download_main_log")
                             
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        flash(f"An error occurred while reading the log file: {e}", "danger")
        return redirect(url_for('index'))

@app.route('/api/logs/download/main')
def download_main_log():
    """Downloads the ACTUAL full log file from disk."""
    try:
        if os.path.exists(LOG_FILE):
            return send_file(LOG_FILE, as_attachment=True, download_name='domain-manager.log')
        else:
            flash("Log file not found on disk.", "danger")
            return redirect(url_for('view_all_logs'))
    except Exception as e:
        logger.error(f"Error downloading log file: {e}")
        flash(f"Error downloading file: {e}", "danger")
        return redirect(url_for('view_all_logs'))

# --- Trigger Routes ---

@app.route('/api/trigger/ddns', methods=['POST'])
def trigger_ddns():
    if config.demo_mode:
        flash("Actions are disabled in Demo Mode.", "info")
        return redirect(url_for('index'))
    try:
        run_ddns_update() 
        flash("Manual DDNS update check initiated.", "info")
    except Exception as e:
        flash(f"An error occurred: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/api/trigger/ssl_renew', methods=['POST'])
def trigger_ssl():
    if config.demo_mode:
        flash("Actions are disabled in Demo Mode.", "info")
        return redirect(url_for('index'))
    try:
        run_ssl_check()
        flash("Manual SSL renewal check initiated.", "info")
    except Exception as e:
        flash(f"An error occurred: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/api/trigger/ssl_create/<domain_name>', methods=['POST'])
def trigger_create_cert(domain_name):
    if config.demo_mode:
        flash("Actions are disabled in Demo Mode.", "info")
        return redirect(url_for('index'))
    
    domain_config = next((d for d in config.get_domains() if d['name'] == domain_name), None)
    if not domain_config:
        flash(f"Could not find config for {domain_name}", "danger")
        return redirect(url_for('index'))
    
    global_notifications_enabled = config.get('notifications', {}).get('enabled', False)
    domain_notifications_enabled = domain_config.get('notifications', True) 
    send_alerts = global_notifications_enabled and domain_notifications_enabled

    try:
        is_wildcard = domain_config.get('ssl', {}).get('wildcard', False)
        success, output = cert_service.create_certificate(domain_name, is_wildcard)
        
        if not success:
            flash(f"Failed to create certificate: {output}", "danger")
            if send_alerts:
                notify_service.send_notification(f"SSL Creation FAILED for {domain_name}", f"Error:\n{output}")
            return redirect(url_for('index'))

        new_expiry_date = cert_monitor.get_cert_expiration_date(domain_name)
        if new_expiry_date:
            app_state['domain_states'][domain_name]['ssl_expiration'] = new_expiry_date
            flash(f"Successfully created certificate for {domain_name}.", "success")
            if send_alerts:
                notify_service.send_notification(f"SSL Created for {domain_name}", f"Expires: {new_expiry_date.strftime('%Y-%m-%d')}")
        else:
            flash(f"Certbot ran, but cert file not found.", "warning")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    
    save_state()
    return redirect(url_for('index'))

@app.route('/api/trigger/test_notification_single', methods=['POST'])
def trigger_test_notification_single():
    try:
        data = request.json
        service = data.get('service')
        url = data.get('url')
        if not service or not url:
            return jsonify({"status": "error", "message": "Missing service name or URL"}), 400
        success, message = notify_service.send_single_test(service, url)
        if success:
            return jsonify({"status": "success", "message": message})
        else:
            return jsonify({"status": "error", "message": message}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/trigger/test_smtp', methods=['POST'])
def trigger_test_smtp():
    """Tests ONLY the SMTP configuration."""
    try:
        notify_service._load_config()
        success, message = notify_service.send_smtp_test_only()
        if success:
            return jsonify({"status": "success", "message": message})
        else:
            return jsonify({"status": "error", "message": message}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/refresh_ip/<domain_name>', methods=['GET'])
def trigger_refresh_ip(domain_name):
    if config.demo_mode:
        flash("Actions are disabled in Demo Mode.", "info")
        return redirect(url_for('index'))
    try:
        ip = r53_service.get_a_record_ip(domain_name)
        if domain_name not in app_state['domain_states']:
             app_state['domain_states'][domain_name] = {}
        app_state['domain_states'][domain_name]['recorded_ip'] = ip
        save_state()
        flash(f"Refreshed Recorded IP for {domain_name}. Value: {ip or 'N/A'}", "info")
    except Exception as e:
        flash(f"Error refreshing IP: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/api/force_update_ip/<domain_name>', methods=['POST'])
def trigger_force_update_ip(domain_name):
    if config.demo_mode:
        flash("Actions are disabled in Demo Mode.", "info")
        return redirect(url_for('index'))
    try:
        domain_config = next((d for d in config.get_domains() if d['name'] == domain_name), None)
        if not (domain_config and domain_config.get('ddns', False)):
             flash(f"Cannot update IP: {domain_name} does not have DDNS enabled.", "danger")
             return redirect(url_for('index'))

        public_ip = app_state.get("public_ip")
        if not public_ip:
            flash("Cannot update IP: Public IP is unknown.", "danger")
            return redirect(url_for('index'))

        success = r53_service.update_a_record_ip(domain_name, public_ip)
        if success:
            app_state['domain_states'][domain_name]['recorded_ip'] = public_ip
            app_state['domain_states'][domain_name]['last_update_time'] = get_current_time_in_tz()
            save_state()
            flash(f"Successfully forced update for {domain_name}.", "success")
        else:
            flash(f"Failed to force update for {domain_name}.", "danger")
    except Exception as e:
        flash(f"An error occurred: {e}", "danger")
    return redirect(url_for('index'))
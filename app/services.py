import logging
import subprocess
import os
import smtplib
import ssl
from email.mime.text import MIMEText
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import requests
import datetime
import cryptography.x509
from cryptography.hazmat.backends import default_backend
import pytz
import apprise
import urllib.parse

# Import the global config object
from app.app import config

logger = logging.getLogger(__name__)

# --- Helper function for getting timezone ---
def get_user_timezone():
    """Gets the pytz timezone object from config."""
    try:
        tz_name = config.get('timezone', 'UTC')
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{tz_name}'. Defaulting to UTC.")
        return pytz.timezone('UTC')

# --- Notification Service ---

class NotificationService:
    """
    Handles sending notifications.
    Refactored to load config dynamically on every send call.
    """
    
    def __init__(self):
        # We no longer load config in __init__ to support dynamic updates
        self.apobj = None
        self.smtp_enabled = False
        self.enabled = False

    def _load_config(self):
        """Re-reads configuration and rebuilds the Apprise object."""
        self.config_data = config.get('notifications', {})
        self.enabled = self.config_data.get('enabled', False)
        self.apobj = apprise.Apprise()
        
        if not self.enabled:
            return

        # --- 1. SMTP Config ---
        self.smtp_config = self.config_data.get('smtp', {})
        self.smtp_enabled = self.smtp_config.get('enabled', False)
        
        if self.smtp_enabled:
            self.smtp_host = self.smtp_config.get('host')
            self.smtp_port = self.smtp_config.get('port')
            self.smtp_from = self.smtp_config.get('from_email', '').strip()
            self.smtp_to = self.smtp_config.get('to_email', '').strip()
            
            # Strictly use settings.json values
            self.smtp_user = self.smtp_config.get('user')
            self.smtp_pass = self.smtp_config.get('pass')
            
            if not all([self.smtp_host, self.smtp_port, self.smtp_user, self.smtp_pass, self.smtp_from, self.smtp_to]):
                logger.warning("SMTP enabled but missing required fields. Disabling.")
                self.smtp_enabled = False

        # --- 2. Apprise Config ---
        def add_url_notifier(service_name):
            service_config = self.config_data.get(service_name, {})
            if service_config.get('enabled'):
                url = service_config.get('url')
                if url:
                    self.apobj.add(url)

        for svc in ['discord', 'slack', 'telegram', 'msteams', 'pushover', 'gchat']:
            add_url_notifier(svc)

    def _send_smtp(self, subject, body):
        """Sends via smtplib."""
        if not self.smtp_enabled:
            return True 

        logger.info(f"Sending email via custom SMTP to {self.smtp_to}...")
        try:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_user, self.smtp_pass)
            
            recipients = [r.strip() for r in self.smtp_to.split(',')]
            
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.smtp_from
            msg['To'] = self.smtp_to

            server.sendmail(self.smtp_from, recipients, msg.as_string())
            server.quit()
            return True
        except Exception as e:
            logger.error(f"SMTP Failed: {e}")
            return False

    def _send_apprise(self, subject, body):
        """Sends via Apprise."""
        if not self.apobj.servers:
            return True
            
        # Apprise returns True if at least one notification worked
        return self.apobj.notify(body=body, title=subject)

    def send_notification(self, subject, body):
        """Public method to send notifications."""
        # Reload config every time to catch changes
        self._load_config()
        
        if not self.enabled:
            return

        self._send_smtp(subject, body)
        self._send_apprise(subject, body)

    def send_test_notification(self):
        """Sends a test notification and returns status."""
        # Reload config to test NEW settings immediately
        self._load_config()
        
        logger.info("Sending test notification...")
        if not self.enabled:
             return False, "Notifications are globally disabled in settings."
             
        subject = "Test Notification - Domain Manager"
        body = "This is a test notification.\n\nIf you received this, your settings are correct."
        
        smtp_ok = self._send_smtp(subject, body)
        apprise_ok = self._send_apprise(subject, body)
        
        if smtp_ok or apprise_ok:
            return True, "Notification sent successfully (via enabled channels)."
        else:
            return False, "Failed to send notification. Check logs."
    
    def send_single_test(self, service_name, url):
        """Tests a single Apprise URL immediately."""
        logger.info(f"Testing single service: {service_name}")
        try:
            # Create a temporary Apprise object just for this test
            temp_ap = apprise.Apprise()
            if not temp_ap.add(url):
                 return False, f"Invalid URL format for {service_name}"
            
            success = temp_ap.notify(
                body=f"This is a test notification for {service_name}.",
                title="Test Notification"
            )
            
            if success:
                return True, f"Test sent to {service_name}!"
            else:
                return False, f"Failed to send to {service_name} (Apprise returned False)."
        except Exception as e:
            logger.error(f"Single test failed: {e}")
            return False, str(e)

    def send_smtp_test_only(self):
        """Tests specifically the SMTP connection and sends an email."""
        if not self.smtp_enabled:
            return False, "SMTP is not enabled in settings."

        logger.info("Testing SMTP configuration...")
        subject = "SMTP Test - Domain Manager"
        body = "This is a test email to verify your SMTP settings.\n\nIf you are reading this, it works!"
        
        if self._send_smtp(subject, body):
            return True, f"Test email sent to {self.smtp_to}"
        else:
            return False, "Failed to send SMTP email. Check container logs for details."

# --- Public IP Service ---
class PublicIPService:
    """Fetches the container's public IP address."""
    
    def __init__(self):
        self.ip_providers = [
            "https://api.ipify.org",
            "https://icanhazip.com",
            "https://ipinfo.io/ip"
        ]
        
    def get_public_ip(self):
        """Tries multiple providers to get the public IP."""
        for provider in self.ip_providers:
            try:
                response = requests.get(provider, timeout=5)
                response.raise_for_status()
                ip = response.text.strip()
                logger.info(f"Public IP successfully retrieved: {ip}")
                return ip
            except requests.RequestException:
                continue
        
        logger.error("All public IP providers failed.")
        return None

# --- Route 53 Service ---
class Route53Service:
    """Handles all interactions with AWS Route 53."""
    
    def __init__(self):
        aws_config = config.get('aws', {})
        
        # 1. Check if keys exist in the config (mapped from ENV in config.py)
        access_key = aws_config.get('access_key_id')
        secret_key = aws_config.get('secret_access_key')

        if not access_key or not secret_key:
             raise Exception("Missing Credentials. For Route53, please provide AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (or USERNAME/PASSWORD) env vars.")

        try:
            self.client = boto3.client(
                'route53',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key
            )
            # 2. Connectivity Check
            self.client.list_hosted_zones(MaxItems='1')
            logger.info("Route 53 client initialized and verified successfully.")
            
        except NoCredentialsError:
            raise Exception("AWS Credentials Invalid or Not Found.")
        except ClientError as e:
            raise Exception(f"AWS Connection Error: {e}")
        except Exception as e:
            raise Exception(f"Route53 Init Error: {e}")

    def _find_hosted_zone_id(self, domain_name):
        try:
            paginator = self.client.get_paginator('list_hosted_zones')
            for page in paginator.paginate():
                for zone in page['HostedZones']:
                    if domain_name.endswith(zone['Name'][:-1]):
                        return zone['Id']
        except Exception as e:
            logger.error(f"Route53 API Error: {e}")
        return None

    def get_a_record_ip(self, domain_name):
        zone_id = self._find_hosted_zone_id(domain_name)
        if not zone_id:
            return None
        
        try:
            response = self.client.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=domain_name,
                StartRecordType='A',
                MaxItems='1'
            )
            record_sets = response.get('ResourceRecordSets', [])
            if record_sets and record_sets[0]['Name'] == f"{domain_name}.":
                record = record_sets[0]
                if 'ResourceRecords' in record:
                    return record['ResourceRecords'][0]['Value']
                elif 'AliasTarget' in record:
                    return f"ALIAS: {record['AliasTarget']['DNSName']}"
            return None
        except ClientError as e:
            logger.error(f"Error getting 'A' record for {domain_name}: {e}")
            return None

    def update_a_record_ip(self, domain_name, new_ip):
        zone_id = self._find_hosted_zone_id(domain_name)
        if not zone_id:
            return False
        try:
            self.client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    'Comment': f'Domain Manager DDNS update to {new_ip}',
                    'Changes': [{'Action': 'UPSERT', 'ResourceRecordSet': {
                        'Name': domain_name, 'Type': 'A', 'TTL': 300,
                        'ResourceRecords': [{'Value': new_ip}],
                    }}]
                }
            )
            return True
        except ClientError as e:
            logger.error(f"Error updating 'A' record for {domain_name}: {e}")
            return False

# --- Certbot Service ---
class CertbotService:
    """A wrapper for running Certbot shell commands."""

    def _run_command(self, command):
        try:
            result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            return False, e.stderr

    def create_certificate(self, domain_name, is_wildcard):
        domain_arg = f"-d {domain_name}"
        if is_wildcard:
            domain_arg += f" -d *.{domain_name}"
            
        config_dir = f"/certs/{domain_name}"
        os.makedirs(config_dir, exist_ok=True)

        # Dynamic lookup of email for notifications
        email = config.get('notifications', {}).get('smtp', {}).get('to_email', 'admin@example.com')

        command = (
            f"certbot certonly --config-dir {config_dir} --work-dir {config_dir} --logs-dir {config_dir} "
            f"--dns-route53 --agree-tos --email {email} --no-eff-email --non-interactive {domain_arg}"
        )
        return self._run_command(command)

    def run_renewal_check(self, domain_name, auto_update_enabled):
        dry_run_flag = "" if auto_update_enabled else "--dry-run"
        config_dir = f"/certs/{domain_name}"
        os.makedirs(config_dir, exist_ok=True)
        
        command = (
            f"certbot renew --config-dir {config_dir} --work-dir {config_dir} --logs-dir {config_dir} "
            f"--dns-route53 {dry_run_flag}"
        )
        return self._run_command(command)

# --- Certificate Monitor Service ---
class CertificateMonitor:
    """Reads certificate files from disk to check expiration."""

    def get_cert_expiration_date(self, domain_key):
        live_dir = f"/certs/{domain_key}/live/"
        
        # DEBUG: Check if main dir exists
        if not os.path.isdir(live_dir):
            logger.warning(f"[{domain_key}] SSL Monitor: Directory not found at {live_dir}")
            return None
        
        cert_path = None
        try:
            # Look for subdirectories (Certbot creates symlink folders inside live)
            subdirs = [d for d in os.listdir(live_dir) if os.path.isdir(os.path.join(live_dir, d))]
            
            if not subdirs:
                logger.warning(f"[{domain_key}] SSL Monitor: No subdirectories found in {live_dir}")
                
            for subdir in subdirs:
                potential_path = os.path.join(live_dir, subdir, "fullchain.pem")
                if os.path.exists(potential_path):
                    cert_path = potential_path
                    break
            
            if not cert_path:
                logger.warning(f"[{domain_key}] SSL Monitor: fullchain.pem not found in any subdir of {live_dir}")
                return None

            # Attempt to read the file
            with open(cert_path, 'rb') as f:
                cert_data = f.read()
            
            cert = cryptography.x509.load_pem_x509_certificate(cert_data, default_backend())
            
            # Convert to User Timezone
            tz = get_user_timezone()
            return cert.not_valid_after_utc.astimezone(tz)
            
        except Exception as e:
            logger.error(f"[{domain_key}] SSL Monitor Error reading {cert_path}: {e}")
            return None
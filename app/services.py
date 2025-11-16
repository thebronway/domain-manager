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
        return pyt_timezone('UTC')

# --- Notification Service ---

class NotificationService:
    """Handles sending all notifications via Apprise AND a custom SMTP handler."""
    
    def __init__(self):
        self.config = config.get('notifications', {})
        self.enabled = self.config.get('enabled', False)
        self.apobj = apprise.Apprise()

        if not self.enabled:
            logger.info("Notifications are globally disabled.")
            return

        logger.info("Initializing notification services...")
        
        # --- 1. SMTP (Special case, using smtplib) ---
        self.smtp_config = self.config.get('smtp', {})
        self.smtp_enabled = self.smtp_config.get('enabled', False)
        if self.smtp_enabled:
            # Load settings, but don't connect yet
            self.smtp_host = self.smtp_config.get('host')
            self.smtp_port = self.smtp_config.get('port')
            self.smtp_user = os.environ.get('SMTP_USER')
            self.smtp_pass = os.environ.get('SMTP_PASS')
            # Use .strip() to fix any whitespace issues in config
            self.smtp_from = self.smtp_config.get('from_email', '').strip()
            self.smtp_to = self.smtp_config.get('to_email', '').strip()
            
            if not all([self.smtp_host, self.smtp_port, self.smtp_user, self.smtp_pass, self.smtp_from, self.smtp_to]):
                logger.error("SMTP is enabled, but config.yml or env vars are missing settings. Disabling.")
                self.smtp_enabled = False
            else:
                logger.info(f"Custom SMTP handler is enabled for {self.smtp_host}.")

        # --- 2. Apprise Notifiers (for everything else) ---
        # Helper function for URL-based notifiers
        def add_url_notifier(service_name):
            service_config = self.config.get(service_name, {})
            if service_config.get('enabled'):
                url = service_config.get('url')
                if url:
                    self.apobj.add(url)
                    logger.info(f"{service_name.capitalize()} notifier added.")
                else:
                    logger.warning(f"{service_name.capitalize()} is enabled but its URL is not set in env vars.")

        add_url_notifier('discord')
        add_url_notifier('slack')
        add_url_notifier('telegram')
        add_url_notifier('msteams')
        add_url_notifier('pushover')
        add_url_notifier('gchat')
        
        # Check if any servers were successfully configured
        if not self.apobj.servers and not self.smtp_enabled:
            logger.warning("Notifications are enabled, but no valid notifiers were successfully configured.")
            self.enabled = False

    def _send_smtp(self, subject, body):
        """
        Sends an email using the proven smtplib logic from test_smtp.py.
        """
        if not self.smtp_enabled:
            return True # Not enabled, so not a failure

        logger.info(f"Sending email via custom SMTP to {self.smtp_to}...")
        
        try:
            # 1. Connect and Log In
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_user, self.smtp_pass)
            
            # 2. Build and Send Message
            # We must split the "to" list in case of multiple recipients
            recipients = [r.strip() for r in self.smtp_to.split(',')]
            
            # Use MIMEText for proper formatting
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.smtp_from
            msg['To'] = self.smtp_to # The display header

            server.sendmail(self.smtp_from, recipients, msg.as_string())
            logger.info("Custom SMTP send successful.")
            server.quit()
            return True
            
        except Exception as e:
            logger.error(f"--- CUSTOM SMTP FAILED ---")
            logger.error(f"The REAL error is: {e}")
            return False
        finally:
            try:
                server.quit() # Ensure connection is closed
            except:
                pass

    def _send_apprise(self, subject, body):
        """
        Sends notifications to all Apprise services (except SMTP).
        """
        if not self.apobj.servers:
            return True # No Apprise servers configured, so not a failure

        logger.info("Sending notifications via Apprise...")
        try:
            success = self.apobj.notify(body=body, title=subject)
            if success:
                logger.info("Apprise notification sent successfully.")
            else:
                logger.error("All Apprise notification services failed.")
            return success
            
        except Exception as e:
            logger.error(f"A critical error occurred during Apprise notification: {e}")
            return False

    def _send(self, subject, body):
        """
        Internal helper function to send all notifications.
        """
        if not self.enabled:
            return True, "Notifications are disabled."

        # Run both handlers
        smtp_success = self._send_smtp(subject, body)
        apprise_success = self._send_apprise(subject, body)

        # Succeed if *at least one* method succeeded
        if smtp_success or apprise_success:
            return True, "Notification sent."
        else:
            return False, "All notification services failed."

    def send_notification(self, subject, body):
        """Sends a standard notification."""
        self._send(subject, body)

    def send_test_notification(self):
        """Sends a test notification and returns a status."""
        logger.info("Sending test notification...")
        subject = "Test Notification"
        body = "This is a test notification from Domain Manager.\nThis should work now.\n\nIf you received this, your notification settings are correct."
        return self._send(subject, body)


# --- Public IP Service ---
class PublicIPService:
    """Fetches the container's public IP address."""
    
    def __init__(self):
        aws_config = config.get('aws', {})
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
                logger.info(f"Public IP successfully retrieved from {provider}: {ip}")
                return ip
            except requests.RequestException as e:
                logger.warning(f"Failed to get IP from {provider}: {e}")
        
        logger.error("All public IP providers failed.")
        return None

# --- Route 53 Service ---
class Route53Service:
    """Handles all interactions with AWS Route 53."""
    
    def __init__(self):
        aws_config = config.get('aws', {})
        try:
            self.client = boto3.client(
                'route53',
                aws_access_key_id=aws_config.get('access_key_id'),
                aws_secret_access_key=aws_config.get('secret_access_key')
            )
            self.client.list_hosted_zones(MaxItems='1')
            logger.info("Route 53 client initialized successfully.")
        except NoCredentialsError:
            logger.critical("FATAL: AWS credentials not found. Check environment variables.")
            raise
        except ClientError as e:
            logger.critical(f"FATAL: Error connecting to Route 53: {e}. Check credentials and permissions.")
            raise

    def _find_hosted_zone_id(self, domain_name):
        """Finds the zone ID for a given domain name."""
        paginator = self.client.get_paginator('list_hosted_zones')
        for page in paginator.paginate():
            for zone in page['HostedZones']:
                if domain_name.endswith(zone['Name'][:-1]):
                    return zone['Id']
        logger.error(f"No hosted zone found for domain: {domain_name}")
        return None

    def get_a_record_ip(self, domain_name):
        """Gets the current IP for a domain's 'A' record."""
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
                    logger.warning(f"{domain_name} is an ALIAS record. DDNS cannot update it.")
                    return f"ALIAS: {record['AliasTarget']['DNSName']}"
                
                else:
                    logger.warning(f"Found record for {domain_name} but it has no 'ResourceRecords' or 'AliasTarget' key.")
                    return None
            
            else:
                logger.info(f"No 'A' record found for {domain_name}")
                return None
                
        except ClientError as e:
            logger.error(f"Error getting 'A' record for {domain_name}: {e}")
            return None

    def update_a_record_ip(self, domain_name, new_ip):
        """Updates a domain's 'A' record to the new IP."""
        zone_id = self._find_hosted_zone_id(domain_name)
        if not zone_id:
            return False
            
        try:
            self.client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    'Comment': f'Domain Manager DDNS update to {new_ip}',
                    'Changes': [
                        {
                            'Action': 'UPSERT',
                            'ResourceRecordSet': {
                                'Name': domain_name,
                                'Type': 'A',
                                'TTL': 300,
                                'ResourceRecords': [{'Value': new_ip}],
                            }
                        }
                    ]
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
        """Helper to run a shell command and capture output."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            logger.info(f"Certbot command successful. stdout: {result.stdout}")
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"Certbot command failed. stderr: {e.stderr}")
            return False, e.stderr

    def create_certificate(self, domain_name, is_wildcard):
        """Runs 'certbot certonly' to create a new certificate."""
        domain_arg = f"-d {domain_name}"
        if is_wildcard:
            domain_arg += f" -d *.{domain_name}"
            
        config_dir = f"/certs/{domain_name}"

        logger.info(f"Ensuring config directory exists at {config_dir}")
        os.makedirs(config_dir, exist_ok=True)

        command = (
            f"certbot certonly "
            f"--config-dir {config_dir} "
            f"--work-dir {config_dir} --logs-dir {config_dir} "
            f"--dns-route53 "
            f"--agree-tos "
            f"--email {config.get('notifications', {}).get('smtp', {}).get('to_email', 'admin@example.com')} "
            f"--no-eff-email "
            f"--non-interactive "
            f"{domain_arg}"
        )
        logger.info(f"Attempting to create certificate for: {domain_name}")
        return self._run_command(command)

    def run_renewal_check(self, domain_name, auto_update_enabled):
        """
        Runs 'certbot renew' for a specific domain's config.
        Uses --dry-run if auto_update is disabled.
        """
        
        dry_run_flag = ""
        if not auto_update_enabled:
            dry_run_flag = "--dry-run"
            logger.info(f"[{domain_name}] Running 'certbot renew' check (DRY RUN)...")
        else:
            logger.info(f"[{domain_name}] Running 'certbot renew' check...")
        
        config_dir = f"/certs/{domain_name}"

        logger.info(f"Ensuring config directory exists at {config_dir}")
        os.makedirs(config_dir, exist_ok=True)
        
        command = (
            f"certbot renew --config-dir {config_dir} "
            f"--work-dir {config_dir} --logs-dir {config_dir} "
            f"--dns-route53 "
            f"{dry_run_flag}"
        )
        return self._run_command(command)

# --- Certificate Monitor Service ---
class CertificateMonitor:
    """Reads certificate files from disk to check expiration."""

    def get_cert_expiration_date(self, domain_key):
        
        live_dir = f"/certs/{domain_key}/live/"
        
        if not os.path.isdir(live_dir):
            logger.info(f"Certificate 'live' directory not found at {live_dir}")
            return None
        
        cert_path = None
        try:
            # Find the first directory inside /live/
            subdirs = [d for d in os.listdir(live_dir) if os.path.isdir(os.path.join(live_dir, d))]
            if not subdirs:
                logger.warning(f"No certificate subdirectories found in {live_dir}")
                return None

            # Find the first valid fullchain.pem
            for subdir in subdirs:
                potential_path = os.path.join(live_dir, subdir, "fullchain.pem")
                if os.path.exists(potential_path):
                    cert_path = potential_path
                    logger.info(f"Found certificate at {cert_path}")
                    break
            
            if not cert_path:
                logger.error(f"Found /live/ directory, but no fullchain.pem in any subdirectory.")
                return None

        except Exception as e:
            logger.error(f"Error scanning for certificate in {live_dir}: {e}")
            return None

        # --- Now, read the file we found ---
        tz = get_user_timezone()
        try:
            with open(cert_path, 'rb') as f:
                cert_data = f.read()
            
            cert = cryptography.x509.load_pem_x509_certificate(cert_data, default_backend())
            
            # Use not_valid_after_utc for a timezone-aware datetime
            utc_expiration = cert.not_valid_after_utc
            
            # Convert to the user's configured timezone
            return utc_expiration.astimezone(tz)
            
        except Exception as e:
            logger.error(f"Error reading certificate file {cert_path}: {e}")
            return None
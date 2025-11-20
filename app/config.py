import os
import json
import yaml
import logging
import shutil

logger = logging.getLogger(__name__)

CONFIG_DIR = "/config"
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
LEGACY_CONFIG = os.path.join(CONFIG_DIR, "config.yml")

class Config:
    """
    Manages application settings.
    Migrates from config.yml + ENV to settings.json on first run.
    Allows reading and writing to settings.json.
    """
    def __init__(self):
        self.settings = {}
        
        # --- DEMO MODE CHECK ---
        self.demo_mode = os.environ.get('DEMO_MODE', 'false').lower() == 'true'
        
        # Ensure config directory exists
        os.makedirs(CONFIG_DIR, exist_ok=True)
        
        # Load settings
        self.load()

    def load(self):
        """
        Loads settings into memory. 
        If settings.json exists, use it.
        If not, migrate from legacy config.yml and environment variables.
        """
        if self.demo_mode:
            # In demo mode, generate fake settings or load defaults
            self.settings = self._get_demo_defaults()
            logger.warning("DEMO MODE: Loaded default/fake settings.")
            return

        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    self.settings = json.load(f)
                logger.info(f"Settings loaded from {SETTINGS_FILE}")
            except Exception as e:
                logger.error(f"Error parsing {SETTINGS_FILE}: {e}. Loading defaults.")
                self.settings = {}
        else:
            logger.info(f"{SETTINGS_FILE} not found. Attempting migration from legacy config...")
            self._migrate_legacy_config()

        # ALWAYS load sensitive system secrets from ENV (AWS, SMTP Pass)
        # These are NOT saved to the JSON file for security, but overlaid in memory.
        self._overlay_system_secrets()

    def save(self, new_settings):
        """
        Saves the provided settings dictionary to settings.json.
        """
        if self.demo_mode:
            logger.info("DEMO MODE: Simulate saving settings.")
            self.settings.update(new_settings)
            return True

        try:
            # Create a copy to save to disk
            data_to_save = new_settings.copy()
            
            # Validating structure briefly
            if 'domains' not in data_to_save:
                data_to_save['domains'] = []
            if 'notifications' not in data_to_save:
                data_to_save['notifications'] = {'enabled': False}

            # Write to disk
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(data_to_save, f, indent=4)
            
            # Update in-memory settings
            self.settings = data_to_save
            
            # Re-apply system secrets (AWS/SMTP) so the app keeps working
            self._overlay_system_secrets()
            
            logger.info("Settings successfully saved to disk.")
            return True
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False

    def _migrate_legacy_config(self):
        """
        One-time migration: Reads config.yml + ENV vars -> writes settings.json
        """
        migrated_data = {
            "timezone": "UTC",
            "ip_check_interval": "5m",
            "log_retention": "3 months",
            "domains": [],
            "notifications": {"enabled": False}
        }

        # 1. Read YAML if it exists
        yaml_config = {}
        if os.path.exists(LEGACY_CONFIG):
            try:
                with open(LEGACY_CONFIG, 'r') as f:
                    yaml_config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Error reading legacy YAML: {e}")

        # 2. Map Basic Fields
        migrated_data['timezone'] = yaml_config.get('timezone', 'UTC')
        migrated_data['ip_check_interval'] = yaml_config.get('ip_check_interval', '5m')
        migrated_data['log_retention'] = yaml_config.get('log_retention', '3 months')
        migrated_data['domains'] = yaml_config.get('domains', [])

        # 3. Map Notifications & Merge ENV vars (The crucial step)
        # In the old system, URLs were in ENV. In the new system, they are in JSON.
        yaml_notifs = yaml_config.get('notifications', {})
        new_notifs = {
            "enabled": yaml_notifs.get('enabled', False),
            "smtp": yaml_notifs.get('smtp', {}),
            # Initialize services
            "discord": {"enabled": False, "url": ""},
            "slack": {"enabled": False, "url": ""},
            "telegram": {"enabled": False, "url": ""},
            "msteams": {"enabled": False, "url": ""},
            "pushover": {"enabled": False, "url": ""},
            "gchat": {"enabled": False, "url": ""}
        }
        
        # List of services and their OLD env var names
        services_map = {
            "discord": "DISCORD_WEBHOOK_URL",
            "slack": "SLACK_WEBHOOK_URL",
            "telegram": "TELEGRAM_URL",
            "msteams": "MSTEAMS_WEBHOOK_URL",
            "pushover": "PUSHOVER_URL",
            "gchat": "GCHAT_WEBHOOK_URL"
        }

        for service, env_key in services_map.items():
            # Check if enabled in YAML
            is_enabled = yaml_notifs.get(service, {}).get('enabled', False)
            # Get URL from ENV
            url_val = os.environ.get(env_key, "")
            
            new_notifs[service] = {
                "enabled": is_enabled,
                "url": url_val # Save the secret to JSON now
            }

        migrated_data['notifications'] = new_notifs

        # 4. Save to JSON
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(migrated_data, f, indent=4)
            logger.info(f"Migration successful. Created {SETTINGS_FILE}")
            self.settings = migrated_data
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    def _overlay_system_secrets(self):
        """
        Injects infrastructure secrets from ENV into memory.
        """
        self.settings['aws'] = {
            "access_key_id": os.environ.get('AWS_ACCESS_KEY_ID'),
            "secret_access_key": os.environ.get('AWS_SECRET_ACCESS_KEY')
        }
        
        self.settings['demo_mode'] = self.demo_mode
        
    def _get_demo_defaults(self):
        """Fake settings for Demo Mode"""
        return {
            "timezone": "America/New_York",
            "ip_check_interval": "5m",
            "log_retention": "3 months",
            "domains": [
                {"name": "demo-server.com", "ddns": True, "ssl": {"enabled": True, "wildcard": True}, "notifications": True, "auto_update": True},
                {"name": "my-blog.net", "ddns": False, "ssl": {"enabled": True, "wildcard": False}, "notifications": False, "auto_update": False}
            ],
            "notifications": {
                "enabled": True,
                "discord": {"enabled": True, "url": "https://discord.com/api/webhooks/fake"},
                "smtp": {"enabled": True, "host": "smtp.fake.com", "port": 587, "from_email": "admin@demo.com", "to_email": "user@demo.com"}
            }
        }

    # --- Public Accessors ---

    def get(self, key, default=None):
        """Helper to get a top-level setting."""
        return self.settings.get(key, default)
        
    def get_domains(self):
        """Helper to get the list of domain configs."""
        return self.settings.get('domains', [])
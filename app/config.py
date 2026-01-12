import os
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_DIR = "/config"
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

class Config:
    """
    Manages application settings.
    Pivots strictly on the 'PROVIDER' environment variable.
    """
    def __init__(self):
        self.settings = {}
        
        # --- 1. PROVIDER LOGIC ---
        self.provider = os.environ.get('PROVIDER', '').lower().strip()
        
        # Check for Demo Mode immediately
        self.demo_mode = (self.provider == 'demo')
        
        if not self.provider:
            logger.warning("PROVIDER environment variable is missing!")
        
        # Ensure config directory exists
        os.makedirs(CONFIG_DIR, exist_ok=True)
        
        # Load settings
        self.load()

    def load(self):
        """
        Loads settings from JSON or Defaults.
        """
        # 1. Load Defaults or JSON
        if self.demo_mode:
            # In Demo Mode, we load specific defaults but keep them editable in memory
            self.settings = self._get_demo_defaults()
            logger.info("DEMO MODE ENABLED (via PROVIDER=demo)")
        elif os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    self.settings = json.load(f)
            except Exception as e:
                logger.error(f"Error parsing {SETTINGS_FILE}: {e}. Loading defaults.")
                self.settings = self._get_default_structure()
        else:
            self.settings = self._get_default_structure()

        # 2. OVERLAY ENVIRONMENT VARIABLES (The Source of Truth for Secrets)
        self._overlay_system_secrets()

    def save(self, new_settings):
        """Saves settings to disk (Real) or Memory (Demo)."""
        
        # Ensure basic structure exists before saving
        if 'domains' not in new_settings: new_settings['domains'] = []
        if 'notifications' not in new_settings: new_settings['notifications'] = {'enabled': False}

        if self.demo_mode:
            logger.info("DEMO MODE: Updating in-memory settings only.")
            # Update the in-memory settings so the UI reflects the changes immediately
            self.settings.update(new_settings)
            # Re-apply secrets (like fake SMTP) so they don't get lost
            self._overlay_system_secrets()
            return True

        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(new_settings, f, indent=4)
            
            self.settings = new_settings
            # Re-apply secrets so the app stays consistent immediately
            self._overlay_system_secrets()
            return True
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False

    def _overlay_system_secrets(self):
        """
        Injects secrets from Environment Variables into the memory settings.
        """
        if 'notifications' not in self.settings:
            self.settings['notifications'] = {'enabled': False, 'smtp': {}}
            
        if self.provider == 'route53':
            self.settings['aws'] = {
                "access_key_id": os.environ.get('AWS_ACCESS_KEY_ID') or os.environ.get('USERNAME'),
                "secret_access_key": os.environ.get('AWS_SECRET_ACCESS_KEY') or os.environ.get('PASSWORD')
            }
        
        if os.environ.get('SMTP_USER'):
            if 'smtp' not in self.settings['notifications']: self.settings['notifications']['smtp'] = {}
            self.settings['notifications']['smtp']['user'] = os.environ.get('SMTP_USER')
            
        if os.environ.get('SMTP_PASS'):
            if 'smtp' not in self.settings['notifications']: self.settings['notifications']['smtp'] = {}
            self.settings['notifications']['smtp']['pass'] = os.environ.get('SMTP_PASS')

        env_map = {
            "DISCORD_WEBHOOK_URL": "discord",
            "SLACK_WEBHOOK_URL": "slack",
            "TELEGRAM_URL": "telegram",
            "MSTEAMS_WEBHOOK_URL": "msteams",
            "PUSHOVER_URL": "pushover",
            "GCHAT_WEBHOOK_URL": "gchat"
        }

        for env_key, service_key in env_map.items():
            val = os.environ.get(env_key)
            if val:
                if service_key not in self.settings['notifications']:
                    self.settings['notifications'][service_key] = {}
                self.settings['notifications'][service_key]['url'] = val
                self.settings['notifications'][service_key]['enabled'] = True

    def _get_default_structure(self):
        return {
            "timezone": "UTC",
            "ip_check_interval": "5m",
            "log_retention": "3 months",
            "cert_management": {
                "enabled": True,
                "check_time": "02:30"
            },
            "domains": [],
            "notifications": {"enabled": False}
        }

    def _get_demo_defaults(self):
        """Initial editable settings for Demo Mode."""
        return {
            "timezone": "America/New_York",
            "ip_check_interval": "5m",
            "log_retention": "3 months",
            "cert_management": {
                "enabled": True,
                "check_time": "02:30"
            },
            "domains": [
                {
                    "name": "demo-server.com", 
                    "ddns": True, 
                    "ssl": {"enabled": True, "wildcard": True}, 
                    "notifications": ["discord", "smtp"], 
                    "auto_update": True
                },
                {
                    "name": "backup-server.org", 
                    "ddns": True, 
                    "ssl": {"enabled": True, "wildcard": True}, 
                    "notifications": ["discord", "smtp"], 
                    "auto_update": True
                },
                {
                    "name": "test-server.xyz", 
                    "ddns": True, 
                    "ssl": {"enabled": False, "wildcard": False}, 
                    "notifications": ["discord", "smtp"], 
                    "auto_update": True
                },
                {
                    "name": "my-blog.net", 
                    "ddns": True, 
                    "ssl": {"enabled": True, "wildcard": False}, 
                    "notifications": [], 
                    "auto_update": True
                }
            ],
            "notifications": {
                "enabled": True,
                "discord": {"enabled": True, "url": "https://discord.com/api/webhooks/fake"},
                "smtp": {"enabled": True, "host": "smtp.demo.mail", "port": 587, "from_email": "admin@demo.com", "to_email": "user@demo.com", "user": "demo", "pass": "demo"}
            }
        }

    def get(self, key, default=None):
        return self.settings.get(key, default)
        
    def get_domains(self):
        return self.settings.get('domains', [])
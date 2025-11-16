import os
import yaml
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = "/config/config.yml"

class Config:
    """
    Handles loading and merging of configuration
    from config.yml and environment variables.
    """
    def __init__(self):
        self.file_config = self._load_file_config()
        self.env_config = self._load_env_config()
        self.settings = self._merge_configs()
        
        # --- DEMO MODE CHECK ---
        # Read the DEMO_MODE environment variable
        self.settings['demo_mode'] = os.environ.get('DEMO_MODE', 'false').lower() == 'true'
        
        if self.settings['demo_mode']:
            logger.warning("="*50)
            logger.warning("DEMO MODE IS ENABLED")
            logger.warning("AWS connections will be skipped and data will be randomized.")
            logger.warning("="*50)

    def _load_file_config(self):
        """Loads the YAML config file."""
        if not os.path.exists(CONFIG_PATH):
            logger.error(f"Config file not found at {CONFIG_PATH}. Exiting.")
            raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")
        
        try:
            with open(CONFIG_PATH, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Error parsing {CONFIG_PATH}: {e}")
            raise

    def _load_env_config(self):
        """Loads sensitive secrets from environment variables."""
        return {
            "aws_access_key_id": os.environ.get('AWS_ACCESS_KEY_ID'),
            "aws_secret_access_key": os.environ.get('AWS_SECRET_ACCESS_KEY'),
            
            # SMTP Secrets
            "smtp_user": os.environ.get('SMTP_USER'),
            "smtp_pass": os.environ.get('SMTP_PASS'),
            
            # Apprise URL Secrets
            "discord_webhook_url": os.environ.get('DISCORD_WEBHOOK_URL'),
            "slack_webhook_url": os.environ.get('SLACK_WEBHOOK_URL'),
            "telegram_url": os.environ.get('TELEGRAM_URL'),
            "msteams_webhook_url": os.environ.get('MSTEAMS_WEBHOOK_URL'),
            "pushover_url": os.environ.get('PUSHOVER_URL'),
            "gchat_webhook_url": os.environ.get('GCHAT_WEBHOOK_URL'),
        }

    def _merge_configs(self):
        """
        Merges file config and env config into a single settings object.
        """
        settings = self.file_config.copy()
        
        # 1. AWS Secrets
        settings['aws'] = {
            "access_key_id": self.env_config['aws_access_key_id'],
            "secret_access_key": self.env_config['aws_secret_access_key']
        }
                
        # 2. Notifications
        if settings.get('notifications', {}).get('enabled'):
            
            # Helper function to reduce repetition
            def merge_service_config(service_name, env_key):
                if settings['notifications'].get(service_name, {}).get('enabled'):
                    if service_name not in settings['notifications']:
                        settings['notifications'][service_name] = {}
                    settings['notifications'][service_name]['url'] = self.env_config[env_key]

            # Merge SMTP secrets (special case)
            if settings['notifications'].get('smtp', {}).get('enabled'):
                if 'smtp' not in settings['notifications']:
                    settings['notifications']['smtp'] = {}
                settings['notifications']['smtp']['user'] = self.env_config['smtp_user']
                settings['notifications']['smtp']['pass'] = self.env_config['smtp_pass']
            
            # Merge Apprise URL services
            merge_service_config('discord', 'discord_webhook_url')
            merge_service_config('slack', 'slack_webhook_url')
            merge_service_config('telegram', 'telegram_url')
            merge_service_config('msteams', 'msteams_webhook_url')
            merge_service_config('pushover', 'pushover_url')
            merge_service_config('gchat', 'gchat_webhook_url')
            
        return settings

    def get(self, key, default=None):
        """Helper to get a top-level setting."""
        return self.settings.get(key, default)
        
    def get_domains(self):
        """Helper to get the list of domain configs."""
        return self.settings.get('domains', [])
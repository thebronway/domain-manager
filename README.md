# Domain Manager

A self-hosted, automated DDNS & SSL Certificate manager for AWS Route 53.

This application provides a simple web dashboard to monitor and manage your domain records. It automatically updates your AWS Route 53 'A' records to match your home's dynamic public IP and uses Let's Encrypt to create and renew SSL certificates for your services.

**Version:** `v0.1`
**GitHub:** [thebronway/domain-manager](https://github.com/thebronway/domain-manager)
**Docker Hub:** [thebronway/domain-manager](https://hub.docker.com/r/thebronway/domain-manager)

---

## Features

* **Dynamic DNS (DDNS):** Automatically polls for your public IP and updates specified AWS Route 53 'A' records.
* **Automated SSL:** Automatically creates and renews Let's Encrypt SSL certificates (including wildcard domains) using the DNS-01 challenge via Route 53.
* **Web Dashboard:** A clean UI to monitor the status of your domains, see your public IP, and check SSL expiration dates.
* **Manual Controls:** Trigger IP updates, SSL renewals, or log lookups for individual domains from the UI.
* **Multi-Service Notifications:** Uses `apprise` to send alerts for all events (IP changes, SSL renewals, failures) to multiple services at once (Email, Discord, Slack, Telegram, etc.).
* **Per-Domain Toggles:** Enable or disable DDNS, SSL, auto-updates, and notifications for each domain individually.
* **Log Management:** Automatically cleans up old Certbot logs to prevent disk fill-up.
* **Thread-Safe State:** Uses threading locks to prevent `app_state.json` corruption.

---

## Requirements

Before you begin, you will need:
* A domain name registered and managed through **AWS Route 53**.
* An **AWS Account** with an IAM user.
* **Docker** and **Docker Compose** installed on your server.

---

## Installation & Setup (Docker Compose)

This is the recommended method. It assumes you are pulling the pre-built image from Docker Hub.

### 1. Create Your Project Directory

Create a folder on your server to hold your configuration files.

```bash
mkdir domain-manager
cd domain-manager
mkdir config
```

### 2. Create the `.env` Secrets File

This file will store all your sensitive API keys and passwords.

Create a new file named `.env` in the `domain-manager` directory:

```ini
# ----- AWS Secrets -----
# The IAM user must have permissions for Route 53.
# See the "IAM Permissions" section below for a policy.
AWS_ACCESS_KEY_ID=YOUR_AWS_ACCESS_KEY
AWS_SECRET_ACCESS_KEY=YOUR_AWS_SECRET_KEY
    
# ----- SMTP Secrets (if using) -----
SMTP_USER=YOUR_SMTP_USERNAME
SMTP_PASS=YOUR_SMTP_PASSWORD
    
# ----- Apprise URL Secrets (if using) -----
# Get these from your notification provider.
DISCORD_WEBHOOK_URL=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
SLACK_WEBHOOK_URL=[https://hooks.slack.com/services/](https://hooks.slack.com/services/)...
TELEGRAM_URL=tgram://YOUR_BOT_TOKEN/YOUR_CHAT_ID
MSTEAMS_WEBHOOK_URL=msteams://TOKEN_A/TOKEN_B/TOKEN_C
PUSHOVER_URL=pushover://USER_KEY@API_TOKEN
GCHAT_WEBHOOK_URL=gchat://WEBHOOK_URL
```

### 3. Create `docker-compose.yml`

Create a file named `docker-compose.yml` in the `domain-manager` directory:

```yaml
version: '3.7'

services:
  domain-manager:
    # Use the pre-built image from Docker Hub
    image: thebronway/domain-manager:latest
    
    container_name: domain-manager
    restart: unless-stopped

    # Expose the web UI on port 8080
    ports:
      - "8080:8080"
    
    # Mount local directories for persistent config, certs, and logs
    volumes:
      - ./config:/config
      - ./certs:/certs
      - ./logs:/logs
    
    # Load all the secrets from the .env file
    env_file:
      - .env

    # Set the timezone to match your config.yml
    environment:
      - TZ=America/New_York
```

### 4. Download and Edit the Configuration

1.  Download the example `config.yml` from the GitHub repository into your `config` folder:
    ```bash
    wget -O config/config.yml [https://raw.githubusercontent.com/thebronway/domain-manager/main/app/config_example/config.yml](https://raw.githubusercontent.com/thebronway/domain-manager/main/app/config_example/config.yml)
    ```
    *(If you don't have `wget`, use `curl -o config/config.yml https://raw.githubusercontent.com/thebronway/domain-manager/main/app/config_example/config.yml`)*

2.  **Edit the config:**
    Open `config/config.yml` and edit it to match your needs. Add your domains, set your timezone, and enable your desired notification services.

### 5. Run the Container

Now that your `docker-compose.yml`, `.env`, and `config/config.yml` are all in place, run the container:

```bash
docker-compose up -d
```

The application will pull the image, start the container, and mount your configuration. Access the web dashboard at `http://<your-server-ip>:8080`.

---

## AWS IAM Permissions

Your `AWS_ACCESS_KEY_ID` must belong to an IAM user with permissions to modify Route 53.

The simplest way to grant this is to create a new IAM user and attach the **`AmazonRoute53FullAccess`** AWS-managed policy.

For a more secure, least-privilege policy, you can create your own policy that limits the user's access to only your specific hosted zone.

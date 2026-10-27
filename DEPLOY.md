# Going live checklist

## 1. Persistent storage (essential)

The database (`chef.db`) and photos (`uploads/`) must live on a disk that survives redeploys. Set `DATA_DIR` to that disk.

- **Render / Railway / Fly.io:** add a persistent disk or volume, mount it at `/data`, set `DATA_DIR=/data`.
- **VPS (Ubuntu):** use a folder such as `/var/lib/chef-site` and set `DATA_DIR` to it.
- **Docker:** `docker run -v chefdata:/data -p 8000:8000 --env-file .env chef-site`

Free hosting tiers with no persistent disk will lose photos and quote requests on redeploy. Do not use them for a client.

## 2. Environment variables

Copy `.env.example` and set the values in your host's dashboard:

- `PRODUCTION=1`, a long random `SECRET_KEY`, and `ADMIN_USER` / `ADMIN_PASSWORD` for the first login
- `FORCE_HTTPS=1` and `TRUST_PROXY=1` when the host sits behind a proxy (most do)
- The `SMTP_*` and `MAIL_FROM` values from your email provider (Gmail needs an app password; a business mailbox or a service like Brevo or Mailgun is better)

Generate a secret key: `python -c "import secrets; print(secrets.token_hex(32))"`

## 3. Start command

`gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT` (already in `Procfile`). Keep one worker: SQLite and the login limiter assume a single process.

## 4. HTTPS (essential)

- Managed hosts (Render, Railway, Fly.io) give you HTTPS automatically once the domain is connected.
- On a VPS, put Caddy in front. A complete config is two lines:

```
chefamara.co.za {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy gets and renews the certificate by itself. Then confirm `http://` redirects to `https://`.

## 5. Backups (essential)

- Automatic: with `PRODUCTION=1` a backup zip is written to `DATA_DIR/backups` every 24 hours (the last 14 are kept).
- Manual: the owner can press **Download backup** in the dashboard at any time.
- Those automatic backups sit on the same disk. Also copy a backup somewhere else weekly (cloud drive, or your host's volume snapshots).
- To restore: unzip, put `chef.db` in `DATA_DIR` and the files from `uploads/` into `DATA_DIR/uploads/`.

## 6. Test before handing over

1. Log in, and change the password when asked.
2. Click **Send a test email** in the dashboard.
3. Submit the quote form on the live site and confirm the email arrives.
4. Upload a hero image, a sharing image and a favicon. Paste the site address into WhatsApp to check the preview.
5. Download a backup and open the zip.

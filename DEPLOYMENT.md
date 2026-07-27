# Deployment: Docker + Apache 2 reverse proxy

Runs the app in a Docker container bound to `127.0.0.1:6337`; Apache terminates
TLS on the public interface and reverse-proxies to it.

## Important: single instance only

`app/scheduler.py` starts an in-process APScheduler on startup (season/schedule
refresh, rollover emails — see `README.md`). **Run exactly one container, one
worker, against the database at a time.** Running multiple replicas or
`--workers >1` means multiple schedulers firing the same jobs and duplicate
emails to users. Scale this vertically (bigger box), not horizontally, unless
the scheduler is first moved out of the request process.

## 1. Prerequisites

- Docker + Docker Compose plugin on the host
- Apache 2 with `proxy`, `proxy_http`, `headers`, and `ssl` modules
- A domain pointed at the host, and (recommended) `certbot` for a free cert

## 2. Configure

Copy `.env.example` to `.env` and fill in real values:

```
cp .env.example .env
```

- `IRCAL_BASE_URL` — set this to the **public HTTPS URL** users will see
  (e.g. `https://calendar.example.com`). It's baked into manage-link and
  calendar-subscribe URLs sent by email, so it must match what Apache serves.
- `IRCAL_SMTP_*` — real SMTP creds, or leave blank to just log emails instead
  of sending (fine for a first smoke test, not for real use).
- Leave `IRCAL_DATABASE_URL` unset in `.env` — `docker-compose.yml` overrides
  it to point at the mounted `/app/data` volume regardless.

## 3. Build and run

```
docker compose build
docker compose up -d
```

This builds the image from the included `Dockerfile`, starts the container
listening on `127.0.0.1:6337` only (not exposed publicly — Apache is the public
entry point), and persists state in two named volumes:

- `ircal-data` → `/app/data` (the SQLite database)
- `ircal-cache` → `/app/cache` (downloaded schedule PDFs, so a restart doesn't
  re-fetch what it already parsed)

Check it's up:

```
docker compose logs -f
curl -I http://127.0.0.1:6337/
```

## 4. Apache vhost

A ready-to-edit vhost is at `deploy/apache-iracing-calendar.conf` — copy it,
swap in your real domain, enable it, and point certbot at it:

```
sudo a2enmod proxy proxy_http headers ssl
sudo cp deploy/apache-iracing-calendar.conf /etc/apache2/sites-available/
sudo sed -i 's/calendar.example.com/YOUR-REAL-DOMAIN/' \
    /etc/apache2/sites-available/apache-iracing-calendar.conf
sudo a2ensite apache-iracing-calendar
sudo systemctl reload apache2
sudo certbot --apache -d YOUR-REAL-DOMAIN
```

`certbot --apache` rewrites the `:443` block to add the certificate paths and
usually replaces the `:80` block's redirect with its own — that's expected.

## 5. Updating

```
git pull
docker compose build
docker compose up -d
```

The SQLite schema is created additively via `SQLModel.metadata.create_all`
(`app/db.py`) on startup — there's no migration framework, so a genuinely
breaking schema change (renamed/removed column) needs a manual `sqlite3`
migration against the volume's `iracing_calendar.db` first.

## 6. Backups

The only state worth backing up is the `ircal-data` volume (the database) —
`ircal-cache` is just re-fetchable PDFs/HTML. To snapshot it:

```
docker run --rm -v ircal-data:/data -v "$PWD":/backup alpine \
    tar czf /backup/ircal-data-backup.tar.gz -C /data .
```

## Troubleshooting

- **Emails aren't sending**: check `docker compose logs` — with SMTP unset,
  sends are logged instead (`app/email.py`), which looks identical to a real
  send failure at a glance. Confirm `IRCAL_SMTP_HOST` is actually set.
- **Manage links point to the wrong host**: `IRCAL_BASE_URL` wasn't updated
  before the container started — fix `.env` and `docker compose up -d` again.
- **502 from Apache**: container isn't up or isn't listening on 6337 yet —
  check `docker compose ps` and `docker compose logs`.

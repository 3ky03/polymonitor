# Nginx Templates

This directory contains public Nginx templates for serving the built frontend
as static files and proxying `/wm-api` to the local Tailscale-backed API.

## Included template

- `polydata-static.conf.example`

## Server contract

The production frontend server is expected to:

- serve static files from `/var/www/polydata`
- use SPA fallback via `try_files ... /index.html`
- proxy `/wm-api/` to the local-machine API over Tailscale
- not build frontend code on the server

The checked-in template uses placeholders instead of private values:

- `__POLYDATA_SERVER_NAME__`
- `__POLYDATA_API_UPSTREAM__`

Example upstream value:

```text
http://<tailscale-ip>:18500
```

## Typical install flow

```bash
sudo mkdir -p /var/www/polydata
sudo cp deploy/nginx/polydata-static.conf.example /etc/nginx/sites-available/polydata
sudo nano /etc/nginx/sites-available/polydata
sudo ln -sf /etc/nginx/sites-available/polydata /etc/nginx/sites-enabled/polydata
sudo nginx -t
sudo systemctl reload nginx
```

## Notes

- GCP only needs Nginx, Tailscale, OpenSSH, rsync, and the static site
  directory.
- The API continues to live on the local machine; this template does not
  change the `/wm-api` topology.

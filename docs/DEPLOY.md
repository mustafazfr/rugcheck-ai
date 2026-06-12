# $0 deploy + SSL (Faz D — ready; waits only for the domain)

Target: one always-free VM + Caddy (auto-TLS) + Cloudflare free in front. Total cost = the domain.

## 1. VM (pick one, both $0)
- **Oracle Cloud Always Free** (best: 4 ARM cores / 24 GB) — Ubuntu 22.04+
- GCP `e2-micro` (us-west1/us-central1/us-east1) — fine for this app (SQLite + async IO)

## 2. Install + run the app
```bash
sudo adduser --disabled-password app && sudo su - app
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/mustafazfr/rugcheck-ai /opt/rugcheck && cd /opt/rugcheck
cp config/config.example.yaml config/config.yaml
cp config/.env.example .env        # then fill: HELIUS_API_KEY (+ payments vars when going live)
uv run pytest -q                   # sanity
```
Edit `config/config.yaml` for production:
```yaml
web:
  rate:
    trust_proxy: true              # behind Caddy/Cloudflare → real client IPs + HSTS turns on
    exempt_ips: ["127.0.0.1", "::1", "YOUR.HOME.IP.HERE"]   # owner scans without limits
```
Install the service (as root): copy `deploy/solscout.service` to `/etc/systemd/system/`,
`systemctl enable --now solscout`.

## 3. SSL — automatic via Caddy
```bash
sudo apt install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # put the real domain in first
sudo systemctl reload caddy
```
That's the whole SSL story: Caddy obtains + renews Let's Encrypt certificates by itself.
The app already sends CSP/nosniff/etc on every response and adds HSTS once `trust_proxy: true`.

## 4. Cloudflare free (optional but recommended)
DNS A record → VM IP, orange-cloud ON, SSL mode **Full (strict)**. Hides the origin IP and absorbs
basic floods. Keep `trust_proxy: true` so per-IP limits see real visitor IPs (first XFF hop).

## 5. Payments go-live (only when the owner says so)
```bash
# in /opt/rugcheck/.env  — PUBLIC address only, never a private key/seed
PAYOUT_WALLET=<owner's real wallet address>
PASS_SECRET=$(openssl rand -hex 32)
```
`config.yaml → payments: enabled: true`, restart. **Dry-run first**: set `payments.rpc_url` to a
devnet RPC and pay yourself 0.1 devnet SOL end-to-end. Never run a test payout wallet in public.

## 6. Watch after launch
- Helius spend: `sqlite3 solscout.db "select * from credit_usage"` vs the Helius dashboard
  (`web:YYYY-MM-DD` rows) — tune `web.helius_daily_budget` in config, never in code.
- Revenue ledger: `sqlite3 solscout.db "select * from pay_intents where status='paid'"`.
- Twitter timeline sections will be absent more often than locally (one shared server IP) — known,
  fail-open, documented in AUDIT-CHECKS.md.
- Branding sweep when the name lands: title/OG absolute URLs/favicon/og.png (case-file style).

#!/bin/bash
# Serverda: bash scripts/deploy-check.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Git ==="
git log -1 --oneline 2>/dev/null || true
grep -q '443:443' docker-compose.yml && echo "[OK] compose: 443 mapped" || echo "[FAIL] compose: 443 yo'q — git pull kerak"
grep -q 'listen 443' docker/nginx/app.conf && echo "[OK] nginx: SSL block" || echo "[FAIL] nginx: SSL yo'q"

echo ""
echo "=== Sertifikat ==="
if sudo test -f /etc/letsencrypt/live/spinthebottletg.com/fullchain.pem; then
  echo "[OK] fullchain.pem"
else
  echo "[FAIL] sertifikat topilmadi"
fi

echo ""
echo "=== Docker ==="
docker compose ps -a

echo ""
echo "=== Portlar (host) ==="
sudo ss -tlnp | grep -E ':80 |:443 ' || true

echo ""
echo "=== Nginx log (oxirgi 15) ==="
docker compose logs nginx --tail 15 2>/dev/null || true

echo ""
echo "=== Local curl ==="
curl -sI -m 5 http://127.0.0.1/ -H "Host: spinthebottletg.com" | head -3 || echo "[FAIL] http localhost"
curl -skI -m 5 https://127.0.0.1/ -H "Host: spinthebottletg.com" | head -3 || echo "[FAIL] https localhost"

echo ""
echo "=== DNS vs server IP ==="
echo -n "DNS: "; dig +short spinthebottletg.com | head -1
echo -n "Server public IP: "; curl -4 -s --max-time 3 ifconfig.me || curl -4 -s --max-time 3 icanhazip.com || echo "?"

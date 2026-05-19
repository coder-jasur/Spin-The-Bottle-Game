#!/bin/bash
# Host nginx to'xtatish (80/443 Docker uchun bo'shatish)
set -euo pipefail
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl stop nginx 2>/dev/null || true
  sudo systemctl disable nginx 2>/dev/null || true
  echo "[OK] nginx stopped and disabled"
else
  echo "[WARN] systemctl yo'q — nginx qo'lda to'xtating"
fi
if command -v ss >/dev/null 2>&1; then
  ss -tlnp | grep -E ':80 |:443 ' || echo "[OK] 80/443 listen yo'q"
fi

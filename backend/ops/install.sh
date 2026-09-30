#!/usr/bin/env bash
# Installs the systemd user units sp-devnode, sp-backend (both enabled: they start at boot
# when the user has lingering, `loginctl enable-linger`) and sp-reset (run on demand:
# `systemctl --user start sp-reset`). Re-run after moving the checkout.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$DEST"
for u in sp-devnode sp-backend sp-reset; do
  sed -e "s|@ROOT@|$ROOT|g" -e "s|@HOME@|$HOME|g" "$ROOT/backend/ops/systemd/$u.service" > "$DEST/$u.service"
done
systemctl --user daemon-reload
systemctl --user enable sp-devnode.service sp-backend.service
echo "installed into $DEST; lingering: $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo unknown)"

#!/usr/bin/env bash
# Installs the systemd user units sp-devnode, sp-backend (both enabled: they start at boot
# when the user has lingering, `loginctl enable-linger`), sp-reset (run on demand:
# `systemctl --user start sp-reset`) and sp-prices (real prices for the mock feeds, off until
# `systemctl --user enable --now sp-prices`). Re-run after moving the checkout.
# With a config.json for another chain (the testnet) it installs sp-backend alone, and
# stops and removes the dev node's units (the chain stays in its docker volume).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND="$ROOT/backend"
. "$BACKEND/ops/network.sh"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$DEST"
unit() { sed -e "s|@ROOT@|$ROOT|g" -e "s|@HOME@|$HOME|g" "$ROOT/backend/ops/systemd/$1.service"; }
if devnode_config; then
  for u in sp-devnode sp-backend sp-reset sp-prices; do
    unit "$u" > "$DEST/$u.service"
  done
  systemctl --user daemon-reload
  systemctl --user enable sp-devnode.service sp-backend.service
else
  # sp-prices feeds the dev node's mock feeds through /demo/feed, which is off on another chain
  for u in sp-devnode sp-prices; do
    systemctl --user disable --now "$u.service" 2>/dev/null || true
  done
  rm -f "$DEST/sp-devnode.service" "$DEST/sp-reset.service" "$DEST/sp-prices.service"
  unit sp-backend | sed -e '/sp-devnode/d' > "$DEST/sp-backend.service" # no Wants/After on the node
  systemctl --user daemon-reload
  systemctl --user enable sp-backend.service
fi
echo "installed into $DEST; lingering: $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo unknown)"

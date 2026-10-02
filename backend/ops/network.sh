# Sourced by the ops scripts (needs $BACKEND). The dev node belongs to a dev-node config:
# with a backend/config.json made for another chain (make-config.py, the testnet) the host
# runs the service alone. A host without a config yet is a dev-node host.
devnode_config() {
  local cfg="${BACKEND_CONFIG:-$BACKEND/config.json}"
  [ ! -f "$cfg" ] || [ "$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["chainId"])' "$cfg")" = 412346 ]
}

#!/usr/bin/env bash
# Writes the interface ABIs to abi/*.json for the frontend (IDeskCover = IDesk + the cover leg).
# Run from anywhere: contracts/script/export-abi.sh
set -euo pipefail
cd "$(dirname "$0")/.."
FORGE="${FORGE:-$(command -v forge || echo "$HOME/.foundry/bin/forge")}"
"$FORGE" build --quiet
for i in ISeriesFactory INoteSeries ISeriesToken INoteQuoter IDesk IDeskCover IFixingsRecorder ISurrogatePricer IAggregatorV3; do
  "$FORGE" inspect "$i" abi --json > "../abi/$i.json"
done
echo "wrote $(ls ../abi | wc -l) ABIs to abi/"

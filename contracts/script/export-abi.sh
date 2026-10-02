#!/usr/bin/env bash
# Writes the interface ABIs to abi/*.json for the frontend (IDeskCover = IDesk + the cover leg; IDeskQueue = the LP redemption queue).
# v2 perpetual note: IPerpFactory, IPerpSeries, IPerpQuoter, IPerpPricer, IPerpDesk (merge with IDeskQueue),
# IWeekendSource and PerpWrapper (a contract: it has no interface of its own). NOTE and WRITER of a
# perpetual series are ISeriesToken, as in v1.
# Run from anywhere: contracts/script/export-abi.sh
set -euo pipefail
cd "$(dirname "$0")/.."
FORGE="${FORGE:-$(command -v forge || echo "$HOME/.foundry/bin/forge")}"
"$FORGE" build --quiet
for i in ISeriesFactory INoteSeries ISeriesToken INoteQuoter IDesk IDeskCover IDeskQueue IFixingsRecorder ISurrogatePricer IAggregatorV3 \
  IPerpFactory IPerpSeries IPerpQuoter IPerpPricer IPerpDesk IWeekendSource PerpWrapper; do
  "$FORGE" inspect "$i" abi --json > "../abi/$i.json"
done
echo "wrote $(ls ../abi | wc -l) ABIs to abi/"

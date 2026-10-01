#!/usr/bin/env bash
# DEV ONLY. Builds sp-nitro-node:v3.11.4-7d5ac27-clock: the stock Nitro dev-node
# image with one change, block timestamp = time.Now() + offset, where the offset
# (seconds) is read from the file named by $SP_CLOCK_OFFSET_FILE (unset/missing
# file -> 0, stock behaviour). See sequencer-clock.patch and docs/backend.md
# ("Dev clock"). Testnet and mainnet run stock Nitro; never use this image there.
#
#   backend/devnode/nitro-clock/build.sh
#
# Steps: shallow-clone OffchainLabs/nitro at v3.11.4 (checks the commit is
# 7d5ac27, the stock image's) with the submodules the node build needs, apply
# the patch, append Dockerfile.clock to Nitro's own Dockerfile and build its
# `sp-nitro-node-clock` stage with BuildKit (needs `docker buildx`). Only the
# stages the `nitro` binary needs run (brotli, contracts, libstylus, Go); the
# result is FROM the stock image with /usr/local/bin/nitro replaced.
#
# Env: NITRO_SRC (checkout dir, default ~/.cache/sp-nitro-src; ~1 GB),
#      IMAGE_TAG (sp-nitro-node:v3.11.4-7d5ac27-clock), NICE (10).
# The BuildKit cache lives in docker's root; `docker builder prune` frees it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NITRO_TAG=v3.11.4
NITRO_COMMIT=7d5ac27
SRC="${NITRO_SRC:-${XDG_CACHE_HOME:-$HOME/.cache}/sp-nitro-src}"
IMAGE_TAG="${IMAGE_TAG:-sp-nitro-node:v3.11.4-7d5ac27-clock}"
NICE="${NICE:-10}"
# what the node binary's build reads; the wasm testsuite and nitro-testnode are skipped
SUBMODULES=(brotli contracts contracts-legacy contracts-local/lib/openzeppelin-contracts
  contracts-local/src/precompiles crates/tools/wasmer go-ethereum safe-smart-account
  crates/wasm-libraries/soft-float/SoftFloat crates/langs/rust crates/langs/c crates/langs/bf)

docker buildx version >/dev/null 2>&1 || {
  echo "docker buildx is missing (Nitro's Dockerfile needs BuildKit); install the buildx CLI plugin" >&2
  exit 1
}

if [ ! -d "$SRC/.git" ]; then
  mkdir -p "$SRC"
  git -C "$SRC" init -q
  git -C "$SRC" remote add origin https://github.com/OffchainLabs/nitro.git
  git -C "$SRC" fetch -q --depth 1 origin tag "$NITRO_TAG"
  git -C "$SRC" checkout -q FETCH_HEAD
fi
head=$(git -C "$SRC" rev-parse --short=7 HEAD)
[ "$head" = "$NITRO_COMMIT" ] || {
  echo "$SRC is at $head, expected $NITRO_TAG = $NITRO_COMMIT" >&2
  exit 1
}
git -C "$SRC" submodule update --init --depth 1 -j 8 "${SUBMODULES[@]}"
for d in contracts contracts-legacy; do
  git -C "$SRC/$d" submodule update --init --recursive --depth 1
done

# (re)apply the patch on a clean tree
git -C "$SRC" checkout -q -- execution/gethexec/sequencer.go
rm -f "$SRC/execution/gethexec/devclock.go"
git -C "$SRC" apply "$HERE/sequencer-clock.patch"
echo "${IMAGE_TAG#*:}" >"$SRC/.nitro-tag.txt"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat "$SRC/Dockerfile" "$HERE/Dockerfile.clock" >"$tmp/Dockerfile"

start=$(date +%s)
nice -n "$NICE" docker buildx build --load -f "$tmp/Dockerfile" --target sp-nitro-node-clock \
  --build-arg version="${IMAGE_TAG#*:}" --build-arg modified=true \
  --build-arg datetime="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t "$IMAGE_TAG" "$SRC"
echo "built $IMAGE_TAG in $(($(date +%s) - start)) s"
docker run --rm "$IMAGE_TAG" --version

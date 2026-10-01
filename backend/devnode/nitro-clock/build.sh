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
# Disk: the build needs ~25 GB of BuildKit cache. With docker's default
# builder (and the containerd image store) that cache sits under
# /var/lib/containerd on the root disk, whatever docker's data-root is. So by
# default the script prunes the whole build cache when it is done
# (KEEP_BUILD_CACHE=1 keeps it). SP_BUILDER=container instead builds in a
# throwaway docker-container builder, whose state is a docker volume (under
# docker's data-root, e.g. /opt/ai/docker on this host), and removes that
# builder and its volume afterwards (not yet exercised on this host). Only the final image (~1.5 GB on disk
# beyond the stock image's layers) stays in the image store.
#
# Env: NITRO_SRC (checkout dir, default ~/.cache/sp-nitro-src; ~1 GB),
#      IMAGE_TAG (sp-nitro-node:v3.11.4-7d5ac27-clock), NICE (10),
#      SP_BUILDER (default: docker's default builder; `container`: see above),
#      KEEP_BUILD_CACHE (0).
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
cat "$SRC/Dockerfile" "$HERE/Dockerfile.clock" >"$tmp/Dockerfile"

BUILDER_ARGS=()
cleanup() {
  rm -rf "$tmp"
  if [ "${KEEP_BUILD_CACHE:-0}" = 1 ]; then return; fi
  if [ "${SP_BUILDER:-}" = container ]; then
    docker buildx rm sp-nitro-clock >/dev/null 2>&1 || true # also removes its state volume
  else
    docker builder prune -af >/dev/null || true # the default builder's cache, ~25 GB after this build
  fi
}
trap cleanup EXIT
if [ "${SP_BUILDER:-}" = container ]; then
  docker buildx inspect sp-nitro-clock >/dev/null 2>&1 ||
    docker buildx create --name sp-nitro-clock --driver docker-container >/dev/null
  BUILDER_ARGS=(--builder sp-nitro-clock)
fi

start=$(date +%s)
nice -n "$NICE" docker buildx build "${BUILDER_ARGS[@]}" --load -f "$tmp/Dockerfile" --target sp-nitro-node-clock \
  --build-arg version="${IMAGE_TAG#*:}" --build-arg modified=true \
  --build-arg datetime="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t "$IMAGE_TAG" "$SRC"
echo "built $IMAGE_TAG in $(($(date +%s) - start)) s"
docker run --rm "$IMAGE_TAG" --version

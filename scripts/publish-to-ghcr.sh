#!/usr/bin/env bash
# publish-to-ghcr.sh — build and optionally publish the dmf-promsd image to GHCR.
#
# The image tag is VERSION-driven and must match the repo root VERSION file.
# By default the script builds locally with `docker buildx build --load`.
# Pass `--push` to publish to GHCR with both `:VERSION` and `:latest` tags.
#
# Usage:
#
#   scripts/publish-to-ghcr.sh
#   scripts/publish-to-ghcr.sh --push
#
# Env knobs:
#   GHCR_USER      GitHub username for `docker login ghcr.io` (required with --push)
#   GHCR_NAMESPACE GHCR namespace (default: dmfdeploy)
#   IMAGE_TAG      Override tag (must still match VERSION)
#   PLATFORMS      Buildx platform list for --push (default: linux/amd64,linux/arm64)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GHCR_NAMESPACE="${GHCR_NAMESPACE:-dmfdeploy}"
IMAGE_REPO="ghcr.io/${GHCR_NAMESPACE}/dmf-promsd"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH=0
BUILDX_BIN="${DOCKER_BUILDX_BIN:-$(command -v docker-buildx || true)}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--push] [--no-push]

  --push      Build and publish to GHCR with :VERSION and :latest tags
  --no-push   Build locally only (default)

Environment:
  GHCR_USER      GitHub username for docker login when --push is set
  GHCR_NAMESPACE default: dmfdeploy
  IMAGE_TAG      default: read from ./VERSION
  PLATFORMS      default: linux/amd64,linux/arm64
EOF
  exit 1
}

for arg in "$@"; do
  case "$arg" in
    --push)
      PUSH=1
      ;;
    --no-push)
      PUSH=0
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      usage
      ;;
  esac
done

if [[ ! -f VERSION ]]; then
  echo "ERROR: VERSION file not found at $REPO_ROOT/VERSION" >&2
  exit 1
fi

REPO_VERSION="$(tr -d '[:space:]' < VERSION)"
IMAGE_TAG="${IMAGE_TAG:-$REPO_VERSION}"

if [[ "$IMAGE_TAG" != "$REPO_VERSION" ]]; then
  cat >&2 <<EOF
ERROR: IMAGE_TAG="$IMAGE_TAG" does not match VERSION="$REPO_VERSION".

The adapter image tag is version-locked to the repo VERSION file.
Update VERSION first if you intend to publish a different release tag.
EOF
  exit 1
fi

IMAGE_VERSION_REF="${IMAGE_REPO}:${IMAGE_TAG}"
IMAGE_LATEST_REF="${IMAGE_REPO}:latest"

echo "═══════════════════════════════════════════════════"
echo "  dmf-promsd GHCR publish"
echo "  Version:  $REPO_VERSION"
echo "  Image:    $IMAGE_VERSION_REF"
echo "  Latest:   $IMAGE_LATEST_REF"
echo "═══════════════════════════════════════════════════"

if [[ "$PUSH" -eq 1 ]]; then
  if [[ -z "$BUILDX_BIN" ]]; then
    echo "ERROR: docker-buildx binary not found on PATH." >&2
    exit 1
  fi

  if [[ -z "${GHCR_USER:-}" ]]; then
    echo "ERROR: GHCR_USER is required when --push is set." >&2
    exit 1
  fi

  if [[ -t 0 ]]; then
    echo "Paste GHCR personal access token (write:packages scope; will not echo):"
    read -r -s GHCR_TOKEN
    echo
  else
    IFS= read -r GHCR_TOKEN
  fi

  if [[ -z "${GHCR_TOKEN:-}" ]]; then
    echo "ERROR: GHCR token not provided." >&2
    exit 1
  fi

  DOCKER_CONFIG="$(mktemp -d)"
  export DOCKER_CONFIG
  cleanup() {
    rm -rf "${DOCKER_CONFIG}"
    unset DOCKER_CONFIG
  }
  trap cleanup EXIT INT TERM

  echo "=== Docker login to ghcr.io ==="
  printf '%s' "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin
  unset GHCR_TOKEN
fi

if [[ "$PUSH" -eq 1 ]]; then
  echo "=== Buildx publish ==="
  "${BUILDX_BIN}" build \
    --platform "${PLATFORMS}" \
    --tag "${IMAGE_VERSION_REF}" \
    --tag "${IMAGE_LATEST_REF}" \
    --push \
    .
else
  echo "=== Local build ==="
  "${BUILDX_BIN}" build \
    --tag "${IMAGE_VERSION_REF}" \
    --tag "${IMAGE_LATEST_REF}" \
    --load \
    .
fi

echo ""
echo "Done: ${IMAGE_VERSION_REF}"

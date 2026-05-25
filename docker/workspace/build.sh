#!/usr/bin/env bash
# Build the gapt-workspace image. Tag: gapt-workspace:latest.
#
# Run from anywhere — uses its own directory as the build context.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_TAG="${GAPT_WORKSPACE_IMAGE_TAG:-gapt-workspace:latest}"

echo "Building ${IMAGE_TAG} from ${DIR}"
docker build -t "${IMAGE_TAG}" "${DIR}"
echo "Done. Set GAPT_WORKSPACE_SANDBOX_IMAGE=${IMAGE_TAG} (or leave default)."

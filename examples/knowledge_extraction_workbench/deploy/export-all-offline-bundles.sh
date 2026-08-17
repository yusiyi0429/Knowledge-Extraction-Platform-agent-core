#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)

VERSION=${WORKBENCH_VERSION:-0.1.3}
IMAGE_REPOSITORY=${WORKBENCH_IMAGE_REPOSITORY:-knowledge-extraction-workbench}
OUTPUT_DIR=${1:-"$REPO_ROOT/dist/knowledge-workbench-offline"}

mkdir -p "$OUTPUT_DIR"

for architecture in amd64 arm64; do
    platform="linux/$architecture"
    image="$IMAGE_REPOSITORY:$VERSION-$architecture"
    output="$OUTPUT_DIR/knowledge-workbench-offline-$VERSION-linux-$architecture.tar"
    echo "生成 $platform 离线部署包..."
    WORKBENCH_PLATFORM="$platform" \
    WORKBENCH_IMAGE="$image" \
        "$SCRIPT_DIR/export-offline-bundle.sh" "$output"
done

CHECKSUM_FILES="knowledge-workbench-offline-$VERSION-linux-amd64.tar knowledge-workbench-offline-$VERSION-linux-arm64.tar"
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$OUTPUT_DIR" && sha256sum $CHECKSUM_FILES > SHA256SUMS)
else
    (cd "$OUTPUT_DIR" && shasum -a 256 $CHECKSUM_FILES > SHA256SUMS)
fi

echo "双架构离线发布物已生成：$OUTPUT_DIR"
cat "$OUTPUT_DIR/SHA256SUMS"

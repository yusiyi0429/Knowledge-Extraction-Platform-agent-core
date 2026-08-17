#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)

IMAGE_NAME=${WORKBENCH_IMAGE:-knowledge-extraction-workbench:0.1.4}
PLATFORM=${WORKBENCH_PLATFORM:-linux/amd64}
PLATFORM_LABEL=$(printf '%s' "$PLATFORM" | tr '/' '-')
OUTPUT=${1:-"$REPO_ROOT/knowledge-workbench-offline-${PLATFORM_LABEL}.tar"}

if ! command -v docker >/dev/null 2>&1; then
    echo "错误：未找到 Docker。" >&2
    exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
    echo "错误：未找到 docker buildx。" >&2
    exit 1
fi

case "$PLATFORM" in
    linux/amd64|linux/arm64) ;;
    *)
        echo "错误：WORKBENCH_PLATFORM 仅支持 linux/amd64 或 linux/arm64。" >&2
        exit 1
        ;;
esac

TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/knowledge-workbench-bundle.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT HUP INT TERM
BUNDLE_DIR="$TMP_ROOT/knowledge-workbench-offline"
mkdir -p "$BUNDLE_DIR"

echo "构建镜像 $IMAGE_NAME ($PLATFORM)..."
docker buildx build \
    --platform "$PLATFORM" \
    --load \
    --tag "$IMAGE_NAME" \
    --file "$SCRIPT_DIR/Dockerfile" \
    "$REPO_ROOT"

echo "导出镜像..."
docker save "$IMAGE_NAME" | gzip -1 > "$BUNDLE_DIR/workbench-image.tar.gz"
cp "$SCRIPT_DIR/compose.offline.yaml" "$BUNDLE_DIR/compose.yaml"
awk -v image="$IMAGE_NAME" -v platform="$PLATFORM" '
    /^WORKBENCH_IMAGE=/ { print "WORKBENCH_IMAGE=" image; next }
    /^WORKBENCH_PLATFORM=/ { print "WORKBENCH_PLATFORM=" platform; next }
    { print }
' "$SCRIPT_DIR/.env.example" > "$BUNDLE_DIR/.env.example"
cp "$SCRIPT_DIR/start-offline.sh" "$BUNDLE_DIR/start-offline.sh"
cp "$SCRIPT_DIR/README.md" "$BUNDLE_DIR/README.md"
chmod +x "$BUNDLE_DIR/start-offline.sh"

git_revision=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'unknown')
source_state=clean
if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null || true)" ]; then
    source_state=dirty
fi
cat > "$BUNDLE_DIR/BUNDLE_INFO.txt" <<EOF
image=$IMAGE_NAME
platform=$PLATFORM
git_revision=$git_revision
source_state=$source_state
created_at_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
EOF

CHECKSUM_FILES="workbench-image.tar.gz compose.yaml .env.example start-offline.sh README.md BUNDLE_INFO.txt"
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$BUNDLE_DIR" && sha256sum $CHECKSUM_FILES > SHA256SUMS)
else
    (cd "$BUNDLE_DIR" && shasum -a 256 $CHECKSUM_FILES > SHA256SUMS)
fi

mkdir -p "$(dirname -- "$OUTPUT")"
tar -C "$TMP_ROOT" -cf "$OUTPUT" knowledge-workbench-offline
echo "离线部署包已生成：$OUTPUT"

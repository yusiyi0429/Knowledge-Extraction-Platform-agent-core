#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "错误：未找到 Docker。请先安装 Docker Engine。" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "错误：未找到 Docker Compose v2 插件。" >&2
    exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c SHA256SUMS
else
    shasum -a 256 -c SHA256SUMS
fi

gzip -dc workbench-image.tar.gz | docker load

bundle_image=$(awk -F= '$1 == "WORKBENCH_IMAGE" {print substr($0, index($0, "=") + 1); exit}' .env.example)
bundle_platform=$(awk -F= '$1 == "WORKBENCH_PLATFORM" {print substr($0, index($0, "=") + 1); exit}' .env.example)
if [ -z "$bundle_image" ] || [ -z "$bundle_platform" ]; then
    echo "错误：离线包 .env.example 缺少镜像或架构信息。" >&2
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "已创建 .env。需要调整端口或内网模型 HTTP 白名单时，请先编辑该文件。"
else
    env_tmp=$(mktemp "${TMPDIR:-/tmp}/knowledge-workbench-env.XXXXXX")
    trap 'rm -f "$env_tmp"' EXIT HUP INT TERM
    awk -v image="$bundle_image" -v platform="$bundle_platform" '
        BEGIN { image_seen = 0; platform_seen = 0 }
        /^WORKBENCH_IMAGE=/ {
            print "WORKBENCH_IMAGE=" image
            image_seen = 1
            next
        }
        /^WORKBENCH_PLATFORM=/ {
            print "WORKBENCH_PLATFORM=" platform
            platform_seen = 1
            next
        }
        { print }
        END {
            if (!image_seen) print "WORKBENCH_IMAGE=" image
            if (!platform_seen) print "WORKBENCH_PLATFORM=" platform
        }
    ' .env > "$env_tmp"
    mv "$env_tmp" .env
    trap - EXIT HUP INT TERM
    echo "已将 .env 切换到 $bundle_image ($bundle_platform)，其余内网配置保持不变。"
fi

docker compose --env-file .env -f compose.yaml up -d
docker compose --env-file .env -f compose.yaml ps

configured_port=$(awk -F= '$1 == "WORKBENCH_PUBLIC_PORT" {print $2}' .env | tail -n 1)
public_port=${WORKBENCH_PUBLIC_PORT:-$configured_port}
public_port=${public_port:-8765}
echo "工作台已启动：http://<内网服务器IP>:${public_port}"

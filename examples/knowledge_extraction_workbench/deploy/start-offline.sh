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

if [ ! -f .env ]; then
    cp .env.example .env
    echo "已创建 .env。需要调整端口或内网模型 HTTP 白名单时，请先编辑该文件。"
fi

docker compose --env-file .env -f compose.yaml up -d
docker compose --env-file .env -f compose.yaml ps

configured_port=$(awk -F= '$1 == "WORKBENCH_PUBLIC_PORT" {print $2}' .env | tail -n 1)
public_port=${WORKBENCH_PUBLIC_PORT:-$configured_port}
public_port=${public_port:-8765}
echo "工作台已启动：http://<内网服务器IP>:${public_port}"

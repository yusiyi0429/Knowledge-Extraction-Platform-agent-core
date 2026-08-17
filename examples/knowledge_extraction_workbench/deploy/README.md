# Docker 与内网离线部署

该部署保持应用现有的单机架构：一个容器同时提供 React 静态页面和 `aiohttp` API，SQLite、上传素材、生成资产、Skill 与 AES-GCM 主密钥都保存在同一个 Docker volume。无需额外部署数据库、Nginx、消息队列或向量库。

> 工作台仍是“本机演示模式”，没有真实账号认证或 RBAC。监听 `0.0.0.0` 时只能放在可信内网，并应通过防火墙或企业网关限制访问来源。

## 方式一：可联网环境直接启动

要求 Docker Engine 24+、Docker Compose v2.20+ 和可访问 Docker Hub、GHCR、npm、PyPI 的网络。

```bash
cd examples/knowledge_extraction_workbench/deploy
cp .env.example .env
docker compose --env-file .env up -d --build
docker compose --env-file .env ps
```

浏览器访问 `http://<服务器IP>:8765`。查看日志和停止服务：

```bash
docker compose --env-file .env logs -f workbench
docker compose --env-file .env down
```

`down` 不会删除数据卷；不要使用 `down -v`，除非明确需要永久删除数据。

## 方式二：完全离线的内网服务器

### 1. 在联网构建机生成离线包

正式发布时建议一次生成两种 CPU 架构：

```bash
examples/knowledge_extraction_workbench/deploy/export-all-offline-bundles.sh
```

默认产物目录为 `dist/knowledge-workbench-offline/`，包含：

- `knowledge-workbench-offline-0.1.3-linux-amd64.tar`：用于 `uname -m` 显示 `x86_64` 的 Intel/AMD 服务器。
- `knowledge-workbench-offline-0.1.3-linux-arm64.tar`：用于 `uname -m` 显示 `aarch64` 或 `arm64` 的 ARM 服务器。
- `SHA256SUMS`：两份外层离线包的校验值；每个包内部另有一份全文件校验清单。

只构建单一架构时仍可使用底层脚本：

```bash
WORKBENCH_PLATFORM=linux/arm64 \
examples/knowledge_extraction_workbench/deploy/export-offline-bundle.sh
```

构建机可以是 Apple Silicon Mac；`buildx` 会分别生成目标 Linux 镜像。两个包的镜像标签分别带 `-amd64` 和 `-arm64`，每个离线包只包含一种 CPU 架构，避免内网服务器导入无用镜像层。

### 2. 复制并在内网服务器启动

先在目标服务器执行 `uname -m`，再将匹配架构的 `.tar` 文件通过批准的介质复制过去。例如 x86_64 服务器：

```bash
tar -xf knowledge-workbench-offline-0.1.3-linux-amd64.tar
cd knowledge-workbench-offline
./start-offline.sh
```

脚本会先校验 `workbench-image.tar.gz` 的 SHA-256，然后执行 `docker load` 和 `docker compose up -d`。这一过程不会访问公网。

如需修改端口，先编辑自动生成的 `.env`，再执行：

```bash
docker compose --env-file .env -f compose.yaml up -d
```

## 内网模型网关

HTTPS 模型地址无需额外配置。若内网模型网关仅提供 HTTP，必须在 `.env` 中显式列出精确主机名或 IP：

```dotenv
WORKBENCH_MODEL_HTTP_HOSTS=model-gateway,192.168.10.20,host.docker.internal
NO_PROXY=localhost,127.0.0.1,workbench,model-gateway,192.168.10.20,host.docker.internal
```

随后在“模型接入”中填写相同主机，例如 `http://model-gateway:8000/v1`。不支持 `*` 或 CIDR 放行；未列入白名单的 HTTP 地址仍会被拒绝。Linux 下访问宿主机模型可使用 `host.docker.internal`，Compose 已将其映射到 host gateway。

私有 HTTPS CA 不应关闭证书校验。可增加一个 Compose override，把 CA 只读挂载到容器，并配置：

```yaml
services:
  workbench:
    environment:
      WORKBENCH_SSL_CERT: /etc/workbench/certs/company-ca.pem
      SAFE_CERT_DIR: /etc/workbench/certs
    volumes:
      - ./certs/company-ca.pem:/etc/workbench/certs/company-ca.pem:ro
```

## 数据、升级与备份

- 持久卷名称：`knowledge-extraction-workbench_workbench-data`。
- 数据目录：容器内 `/var/lib/knowledge-workbench`。
- API Key 仅通过页面录入，使用卷内 `master.key` 加密；不要单独丢失或替换该文件。
- 升级时导入新镜像并再次执行 `docker compose up -d`，数据卷不会变化。

备份整个数据目录：

```bash
docker compose --env-file .env -f compose.yaml stop workbench
docker compose --env-file .env -f compose.yaml cp \
  workbench:/var/lib/knowledge-workbench ./knowledge-workbench-backup
docker compose --env-file .env -f compose.yaml start workbench
```

备份和恢复期间都应停止容器，并将 SQLite、`master.key`、上传文件和资产作为同一份备份整体处理，不能只复制数据库。

## 健康检查与排障

```bash
curl http://127.0.0.1:8765/api/v1/health
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=200 workbench
```

健康响应应包含 `"status": "ok"`。若浏览器能打开但模型测试失败，优先从容器内检查模型 DNS、端口、代理、HTTP 白名单或私有 CA，而不是关闭 TLS 校验。

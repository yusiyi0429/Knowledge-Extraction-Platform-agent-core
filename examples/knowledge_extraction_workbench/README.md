# 知识萃取智能体工作台

这是一个与 `openjiuwen.core` 公共 API 隔离的完整示例应用。它把场景探索、素材管理、知识萃取与对齐、资产生成和发布串成一个本机可运行工作台。

## 当前能力

- React + Vite + TypeScript 前端，Hash 路由，不依赖 Router、Redux、Query 或 UI 组件库。
- `aiohttp + SQLModel + SQLite` 后端，生产构建由同一进程提供静态文件。
- 使用 `AutoFileParser` 解析 PDF、DOCX、XLSX，并支持 CSV、TSV、TXT、MD。
- 跨素材轮询配额与全文分桶取样，过滤短碎片，不采用“只取前 N 个 Chunk”。
- 产品不创建或展示 Fake Model；模型任务必须挂载已配置的真实 Provider。
- 新任务通过 `openjiuwen.core.foundation.llm.Model` 执行，并使用结构化 JSON 输出、常见格式容错与一次携带原素材上下文的修复重试。
- 对齐页面提供完整 AI 修改助手：一致性检查、监管对齐、查漏补缺、自然语言指令、差异预览、采纳/放弃与失败重试。
- 7 个按需智能体按原型分为 2 个萃取/对齐能力和 5 个生成能力，支持场景级覆盖与配置导入导出。
- Skill 库提供 6 个只读模板，以及可复制、编辑、下载、上传新版本和查看版本历史的场景实例。
- SSE 任务事件、冻结配置、服务重启失败恢复、乐观修订并发、软归档和发布后不可变。
- 生成规则 Excel、决策研判链 Markdown、openJiuwen Skill ZIP、QA JSONL 和合成评测 JSONL。
- API Key 使用 AES-GCM 加密，主密钥文件权限为 `0600`；查询接口只返回 `has_api_key`。
- 登录与用户管理仅为浏览器 `localStorage` 演示，页面明确提示其不构成认证或权限边界。

## 快速启动

先构建前端：

```bash
cd examples/knowledge_extraction_workbench/frontend
npm install
npm run build
```

回到仓库根目录启动后端：

```bash
uv run python -m examples.knowledge_extraction_workbench.backend
```

浏览器打开 `http://127.0.0.1:8765`。首次进入只需填写本机显示名称。

默认数据目录是 `~/.openjiuwen/knowledge-workbench/`，可在启动前覆盖：

```bash
WORKBENCH_DATA_DIR=/path/to/local-data \
uv run python -m examples.knowledge_extraction_workbench.backend
```

Vite 开发模式：

```bash
# 终端 1：仓库根目录
uv run python -m examples.knowledge_extraction_workbench.backend

# 终端 2
cd examples/knowledge_extraction_workbench/frontend
npm run dev
```

开发服务器会把 `/api` 代理到 `127.0.0.1:8765`。

## 模型接入

工作台不会创建内置模型。接入真实模型时：

1. 在“模型接入”新增 Provider、API 地址、模型名称与 API Key。
2. 先点击“测试”，执行不包含素材正文的最小连接调用。
3. 在“智能体与 Skill”选择任意已启用连接，批量应用到 7 个能力；也可按场景、按智能体逐项配置。
4. 之后启动的新 Job 会冻结 Provider、API 地址、模型名称、加密凭据、Skill、参数、素材哈希和模板版本。

产品不会把 DeepSeek 或其他 Provider 写死为默认；存在多个可用连接时，批量应用必须明确选择模型。外部 API 地址必须使用 HTTPS；HTTP 只允许 `localhost`、`127.0.0.1` 或 `::1`。
服务会自动使用系统受信任 CA 并保持严格 TLS 校验；私有 CA 可通过 `WORKBENCH_SSL_CERT` 指定，且证书必须位于 `SAFE_CERT_DIR` 内。

## 验证

```bash
# Python 逻辑与 API 纵向流程
uv run pytest -q \
  tests/unit_tests/examples/knowledge_extraction_workbench/test_pipeline.py \
  tests/unit_tests/examples/knowledge_extraction_workbench/test_model_runtime.py \
  tests/system_tests/examples/knowledge_extraction_workbench/test_workbench_api.py

# Python 静态检查
uv run ruff check \
  examples/knowledge_extraction_workbench/backend \
  tests/unit_tests/examples/knowledge_extraction_workbench \
  tests/system_tests/examples/knowledge_extraction_workbench

# 前端类型与生产构建
cd examples/knowledge_extraction_workbench/frontend
npm run typecheck
npm run build

# 浏览器端到端
npm run test:e2e
```

自动化测试通过 `WORKBENCH_TEST_MODEL=deterministic` 或应用工厂参数显式注入确定性测试替身；它不会写入产品模型列表，也不读取真实 API Key。发布前可另行执行真实模型冒烟验证。

## 明确边界

该示例不提供 RBAC、真实账号认证、消息队列、分布式 Worker、向量数据库、通知中心或外部平台导出。已发布轮次不可原地修改；继续演进时创建下一轮并继承启用素材。

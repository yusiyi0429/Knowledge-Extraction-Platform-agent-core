# Knowledge Extraction Platform · agent-core

基于 openJiuwen agent-core 构建的本机知识萃取工作台。平台将多格式素材转换为可追溯、可修订、可发布的规则、流程、Skill、QA 和评测资产。

当前版本不内置产品 Fake Model；场景探索、萃取、对齐和生成任务使用在页面中配置并挂载的真实模型连接。

## 核心能力

- **场景探索**：上传多份素材，按素材轮询和全文分桶公平取样，生成带来源引用的候选场景。
- **场景与素材**：维护目标、子场景和素材角色，支持素材停用、场景归档及新萃取轮次。
- **知识萃取与对齐**：受控 Map/Reduce 萃取、SSE 进度、Markdown 研判文档，以及支持一致性检查、监管对齐、查漏补缺和自然语言改写的 AI 修改助手。
- **资产生成与发布**：生成规则 Excel、决策研判链 Markdown、openJiuwen Skill ZIP、QA JSONL 和合成评测 JSONL。
- **能力配置**：按原型分为 2 个萃取/对齐智能体与 5 个生成智能体，支持场景级覆盖、导入导出、模型、Skill 和必要参数配置。
- **Skill 工作台**：内置 6 个只读方法论模板，支持复制场景实例、编辑元数据、下载包、上传新版本与查看版本历史。
- **模型接入**：支持 Provider、API 地址和模型配置；API Key 使用 AES-GCM 加密保存且永不回传明文。
- **可审计生命周期**：任务冻结模型、Skill、参数、素材 ID/哈希和模板版本；发布轮次不可原地修改。

## 技术架构

```text
React + Vite + TypeScript
          │  /api/v1 + SSE
          ▼
aiohttp + SQLModel + SQLite
          │
          ├─ AutoFileParser / Chunker
          ├─ openJiuwen Model（已配置的真实 Provider）
          └─ DeterministicTestModel（仅显式注入自动化测试）
```

工作台作为独立示例位于：

```text
examples/knowledge_extraction_workbench/
├── backend/      # API、任务、存储、模型运行时和资产生成
├── frontend/     # React 工作台与 Playwright 验收
└── README.md     # 完整使用说明
```

实现保持在示例目录内，不修改 `openjiuwen.core` 公共 API。

## 快速开始

### 环境要求

- Python `>= 3.11, < 3.14`
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 或更高版本

### 安装与构建

```bash
uv sync

cd examples/knowledge_extraction_workbench/frontend
npm ci
npm run build

cd ../../..
uv run python -m examples.knowledge_extraction_workbench.backend
```

浏览器打开：<http://127.0.0.1:8765>

首次进入只需要填写本机显示名称。执行模型任务前，请先在“模型接入”中保存真实连接，再在“智能体与 Skill”一键挂载到 7 个能力。

### 数据目录

默认数据写入：

```text
~/.openjiuwen/knowledge-workbench/
```

可通过环境变量覆盖：

```bash
WORKBENCH_DATA_DIR=/path/to/local-data \
uv run python -m examples.knowledge_extraction_workbench.backend
```

## 使用流程

### 场景探索

1. 在工作台点击“场景探索”。
2. 输入探索目标并上传一份或多份素材。
3. 分析候选场景，选择“带入新场景”。
4. 启动萃取、复核研判文档并生成资产。

### 直接新建场景

1. 点击“新建场景”直接进入场景工作区，填写目标和子场景。
2. 上传素材并进入知识萃取。
3. 人工保存文档，或通过 AI 修改助手发起快捷检查/自然语言改写，再审阅并采纳或放弃差异建议。
4. 生成五类资产，确认后发布不可变快照。

支持的素材格式：`.pdf`、`.docx`、`.xlsx`、`.csv`、`.tsv`、`.txt`、`.md`。不支持旧版 `.doc` 和 `.xls`。

## 接入模型

可接入 DeepSeek、OpenAI、OpenRouter、SiliconFlow 等当前运行时支持的 Provider：

1. 在“模型接入”页面新增 Provider、HTTPS API 地址、模型名称和 API Key。
2. 使用“测试连接”执行不包含素材正文的最小请求。
3. 在“智能体与 Skill”中选择任意已启用连接，批量应用到 7 个能力；也可按场景、按智能体分别配置。
4. 后续新任务会冻结此次模型和 Skill 配置。

产品不会把任何 Provider 设为固定默认；存在多个可用连接时，批量应用必须明确选择模型。密钥只加密保存在本机数据目录，模型查询接口仅返回 `has_api_key`。请勿把数据目录、密钥文件或 `.env` 提交到版本库。

## 验证

```bash
# 工作台逻辑、API 流程及 agent-core 解析器/模型配置基线
uv run pytest -q \
  tests/unit_tests/core/retrieval/indexing/processor/parser/test_auto_file_parser.py \
  tests/unit_tests/core/foundation/llm/test_model_client_config.py \
  tests/unit_tests/examples/knowledge_extraction_workbench/test_pipeline.py \
  tests/unit_tests/examples/knowledge_extraction_workbench/test_model_runtime.py \
  tests/system_tests/examples/knowledge_extraction_workbench/test_workbench_api.py

# Python 静态检查
uv run ruff check \
  examples/knowledge_extraction_workbench/backend \
  tests/unit_tests/examples/knowledge_extraction_workbench \
  tests/system_tests/examples/knowledge_extraction_workbench

# 前端类型、构建与浏览器验收
cd examples/knowledge_extraction_workbench/frontend
npm run typecheck
npm run build
npm run test:e2e
```

浏览器验收覆盖 `1440×1000` 和 `1280×800`。自动化测试通过 `WORKBENCH_TEST_MODEL=deterministic` 显式注入确定性测试替身，它不会写入模型列表，也不会读取真实 API Key。

## 安全与边界

- 登录和用户管理是明确标注的本机演示交互，不构成认证或权限边界。
- 上传文件执行扩展名、大小和哈希校验；Skill ZIP 检查路径穿越、符号链接、文件数和解压大小。
- 日志和错误响应不记录 API Key、完整提示词或素材正文。
- 当前版本不建设 RBAC、真实多用户认证、消息队列、分布式 Worker、向量数据库或通知中心。

## openJiuwen agent-core

本仓库保留完整的 openJiuwen agent-core SDK，工作台复用了其中的文件解析、Chunker、模型与结构化输出能力。agent-core 的更多开发资料位于 [`docs/`](docs/) 和 [`examples/`](examples/)。

## License

本项目沿用 [Apache License 2.0](LICENSE)。第三方开源软件声明见 [Open_Source_Software_Notice.txt](Open_Source_Software_Notice.txt)。

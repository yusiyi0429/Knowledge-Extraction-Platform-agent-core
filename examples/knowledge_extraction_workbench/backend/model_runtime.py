"""Optional openJiuwen Model adapter used when a real provider is mounted."""

from __future__ import annotations

import json
import re
from typing import Any

import openai
from json_repair import repair_json
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    UserMessage,
)

from .config import trusted_ca_bundle
from .errors import WorkbenchError
from .pipeline import ChunkRef


def build_model_client_config(
    *,
    provider: str,
    api_key: str,
    api_base: str,
    timeout: float,
    max_retries: int,
) -> ModelClientConfig:
    """Build a real-provider client with strict TLS verification enabled."""

    return ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
        timeout=timeout,
        max_retries=max_retries,
        verify_ssl=True,
        ssl_cert=trusted_ca_bundle(),
    )


def model_connection_error(exc: Exception) -> WorkbenchError:
    """Map provider failures to stable, secret-safe workbench errors."""

    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__

    reason = type(chain[-1]).__name__
    if any(isinstance(item, openai.AuthenticationError) for item in chain):
        return WorkbenchError(
            "MODEL_AUTHENTICATION_FAILED",
            "模型服务拒绝了 API Key，请重新保存有效密钥。",
            status=401,
            details={"reason": reason},
        )
    if any(isinstance(item, openai.PermissionDeniedError) for item in chain):
        return WorkbenchError(
            "MODEL_ACCESS_DENIED",
            "当前 API Key 没有调用该模型的权限。",
            status=403,
            details={"reason": reason},
        )
    if any(isinstance(item, openai.NotFoundError) for item in chain):
        return WorkbenchError(
            "MODEL_NOT_FOUND",
            "模型服务地址或模型名称不存在，请检查后重试。",
            status=422,
            details={"reason": reason},
        )
    if any(isinstance(item, openai.RateLimitError) for item in chain):
        return WorkbenchError(
            "MODEL_RATE_LIMITED",
            "模型服务当前限流，请稍后重试。",
            status=429,
            retryable=True,
            details={"reason": reason},
        )
    if any(isinstance(item, openai.APITimeoutError) for item in chain):
        return WorkbenchError(
            "MODEL_REQUEST_TIMEOUT",
            "连接模型服务超时，请检查网络后重试。",
            status=504,
            retryable=True,
            details={"reason": reason},
        )
    if any(isinstance(item, openai.APIConnectionError) for item in chain):
        return WorkbenchError(
            "MODEL_UNAVAILABLE",
            "无法连接模型服务，请检查 API 地址和网络。",
            status=502,
            retryable=True,
            details={"reason": reason},
        )
    if any(isinstance(item, openai.BadRequestError) for item in chain):
        return WorkbenchError(
            "MODEL_REQUEST_INVALID",
            "模型服务拒绝了测试请求，请检查模型名称和 API 地址。",
            status=422,
            details={"reason": reason},
        )
    if any(isinstance(item, BaseError) and item.code in {181000, 181002, 181003, 181005} for item in chain):
        return WorkbenchError(
            "MODEL_CONFIGURATION_INVALID",
            "模型客户端配置无法初始化，请检查调用适配器、地址和本机 TLS 配置。",
            status=422,
            details={"reason": reason},
        )
    return WorkbenchError(
        "MODEL_REQUEST_FAILED",
        "模型连接测试失败，请检查调用适配器、地址、模型名称和密钥。",
        status=502,
        retryable=True,
        details={"reason": reason},
    )


class OpenJiuwenKnowledgeModel:
    """Translate workbench operations to structured openJiuwen Model calls."""

    def __init__(
        self,
        *,
        provider: str,
        api_base: str,
        model_name: str,
        api_key: str,
        temperature: float = 0.2,
    ) -> None:
        self.model_id = f"{provider}:{model_name}"
        self._model_name = model_name
        self._temperature = temperature
        client_config = build_model_client_config(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            timeout=90,
            max_retries=1,
        )
        request_config = ModelRequestConfig(model=model_name, temperature=temperature, max_tokens=8192)
        self._model = Model(client_config, request_config)

    @staticmethod
    def _decode_json(content: Any) -> Any | None:
        if isinstance(content, dict):
            return content
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            if text_parts:
                content = "\n".join(text_parts)
            else:
                return content
        if not isinstance(content, str):
            return None
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().lower() in {"```", "```json"}:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        candidates = [text]
        object_start, object_end = text.find("{"), text.rfind("}")
        array_start, array_end = text.find("["), text.rfind("]")
        if object_start >= 0 and object_end > object_start:
            candidates.append(text[object_start : object_end + 1])
        if array_start >= 0 and array_end > array_start:
            candidates.append(text[array_start : array_end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    parsed = repair_json(candidate, return_objects=True)
                except (TypeError, ValueError):
                    continue
            if isinstance(parsed, (dict, list)):
                return parsed
        return None

    async def _invoke_json(self, instruction: str, context: str) -> Any:
        messages = [
            SystemMessage(
                content=(
                    "你是知识萃取工作台中的结构化处理能力。只返回合法 JSON，不输出 Markdown 代码围栏。"
                    "保留输入中的 material_id、material_name 与 chunk_index，不虚构来源。"
                )
            ),
            UserMessage(content=f"{instruction}\n\n原始上下文：\n{context}"),
        ]
        invalid_content = ""
        for attempt in range(2):
            current_messages = messages
            if attempt:
                current_messages = messages + [
                    UserMessage(
                        content=(
                            "上一次响应未通过 JSON 结构校验。请基于上方完全相同的原始上下文修复输出，"
                            "不要省略来源字段。无效响应摘要：" + invalid_content[:500]
                        )
                    )
                ]
            try:
                response = await self._model.invoke(
                    current_messages,
                    model=self._model_name,
                    temperature=self._temperature,
                    timeout=90,
                )
            except Exception as exc:
                raise model_connection_error(exc) from exc
            parsed = self._decode_json(response.content)
            if parsed is not None:
                return parsed
            invalid_content = response.content if isinstance(response.content, str) else str(response.content)
        raise WorkbenchError(
            "MODEL_JSON_INVALID",
            "模型连续两次未返回符合约定的 JSON 结构。",
            status=422,
            retryable=True,
        )

    @staticmethod
    def _chunk_context(chunks: list[ChunkRef]) -> str:
        return "\n\n".join(
            (
                f"[material_id={chunk.material_id}; material_name={chunk.material_name}; "
                f"chunk_index={chunk.chunk_index}]\n{chunk.text}"
            )
            for chunk in chunks
        )

    @staticmethod
    def _normalize_sources(value: Any, chunks: list[ChunkRef]) -> list[dict[str, Any]]:
        known = {(chunk.material_id, chunk.chunk_index): chunk for chunk in chunks}
        result = []
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("material_id", "")), int(item.get("chunk_index", -1)))
                chunk = known.get(key)
                if chunk:
                    result.append(chunk.source_ref())
        if not result and chunks:
            result.append(chunks[0].source_ref())
        return result

    async def explore(self, chunks: list[ChunkRef]) -> list[dict[str, Any]]:
        payload = await self._invoke_json(
            (
                "识别 1 到 5 个可落地业务场景。返回对象："
                '{"candidates":[{"name":"...","description":"...","goal":"...",'
                '"confidence":0.0,"source_refs":[{"material_id":"...","chunk_index":0}]}]}。'
            ),
            self._chunk_context(chunks),
        )
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型返回的 candidates 结构无效。", status=422, retryable=True)
        normalized = []
        for item in candidates[:5]:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            normalized.append(
                {
                    "name": str(item["name"])[:160],
                    "description": str(item.get("description", ""))[:500],
                    "goal": str(item.get("goal", ""))[:500],
                    "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                    "source_refs": self._normalize_sources(item.get("source_refs"), chunks),
                }
            )
        if not normalized:
            raise WorkbenchError(
                "EXPLORATION_CANDIDATES_EMPTY",
                "模型调用成功，但未返回可用候选场景。",
                status=422,
                retryable=True,
            )
        return normalized

    async def map_chunk(self, chunk: ChunkRef, sequence: int) -> dict[str, Any]:
        payload = await self._invoke_json(
            (
                "从片段提取规则。返回对象："
                '{"rules":[{"title":"...","condition":"...","action":"...",'
                '"exceptions":"...","sources":[{"material_id":"...","chunk_index":0}]}]}。'
            ),
            self._chunk_context([chunk]),
        )
        rows = payload.get("rules") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型返回的 rules 结构无效。", status=422, retryable=True)
        rules = []
        for index, item in enumerate(rows[:4], start=1):
            if not isinstance(item, dict) or not str(item.get("action", "")).strip():
                continue
            rules.append(
                {
                    "title": str(item.get("title", f"规则 {sequence}-{index}"))[:120],
                    "condition": str(item.get("condition", "原文未明确条件"))[:500],
                    "action": str(item["action"])[:1000],
                    "exceptions": str(item.get("exceptions", "原文未明确例外，需业务复核"))[:500],
                    "sources": self._normalize_sources(item.get("sources"), [chunk]),
                }
            )
        return {"rules": rules, "source": chunk.source_ref()}

    async def reduce(self, mapped: list[dict[str, Any]], scene_name: str) -> dict[str, Any]:
        def key(value: Any) -> str:
            return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()

        rules: list[dict[str, Any]] = []
        rules_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        actions_by_scope: dict[tuple[str, str], set[str]] = {}
        scope_labels: dict[tuple[str, str], str] = {}
        for batch in mapped:
            rows = batch.get("rules", []) if isinstance(batch, dict) else []
            if not isinstance(rows, list):
                continue
            for item in rows:
                if not isinstance(item, dict) or not str(item.get("action", "")).strip():
                    continue
                title = str(item.get("title") or f"规则 {len(rules) + 1}")[:120]
                condition = str(item.get("condition") or "原文未明确条件")[:500]
                action = str(item["action"])[:1000]
                exceptions = str(item.get("exceptions") or "原文未明确例外，需业务复核")[:500]
                sources = item.get("sources", []) if isinstance(item.get("sources"), list) else []
                rule_key = (key(title), key(condition), key(action))
                scope_key = rule_key[:2]
                actions_by_scope.setdefault(scope_key, set()).add(rule_key[2])
                scope_labels.setdefault(scope_key, title)
                existing = rules_by_key.get(rule_key)
                if existing is not None:
                    known_sources = {
                        json.dumps(source, ensure_ascii=False, sort_keys=True)
                        for source in existing["sources"]
                        if isinstance(source, dict)
                    }
                    for source in sources:
                        if not isinstance(source, dict):
                            continue
                        source_key = json.dumps(source, ensure_ascii=False, sort_keys=True)
                        if source_key not in known_sources:
                            existing["sources"].append(source)
                            known_sources.add(source_key)
                    continue
                rule = {
                    "id": f"R-{len(rules) + 1:03d}",
                    "title": title,
                    "condition": condition,
                    "action": action,
                    "exceptions": exceptions,
                    "sources": [source for source in sources if isinstance(source, dict)],
                }
                rules.append(rule)
                rules_by_key[rule_key] = rule
        if not rules:
            raise WorkbenchError("EXTRACTION_RESULT_EMPTY", "模型未返回可用规则。", status=422, retryable=True)
        rules = rules[:30]
        process = [
            {
                "step": index,
                "name": rule["title"],
                "description": rule["action"],
                "sources": rule["sources"],
            }
            for index, rule in enumerate(rules[:20], start=1)
        ]
        conflicts = [
            f"“{scope_labels[scope_key]}”在相同适用条件下存在不同执行动作，需人工复核。"
            for scope_key, actions in actions_by_scope.items()
            if len(actions) > 1
        ]
        return {
            "schema_version": "1.0",
            "scene": scene_name,
            "rules": rules,
            "process": process,
            "conflicts": conflicts,
            "generated_by": self.model_id,
        }

    async def suggest(
        self,
        markdown: str,
        structured: dict[str, Any],
        revision: int,
        *,
        mode: str = "CONSISTENCY",
        instruction: str = "",
    ) -> dict[str, Any]:
        mode_guidance = {
            "CONSISTENCY": "检查规则之间的前提、结论、例外和人工升级条件是否互相矛盾。",
            "REGULATORY": "依据当前素材引用检查监管口径是否一致；没有素材依据时不得虚构外部条款。",
            "GAP": "查找当前规则或流程遗漏的常见分支、边界条件和异常处理。",
            "CUSTOM": "严格按照用户给出的修改意图定位并改写对应段落。",
        }
        requested_change = instruction.strip() or mode_guidance.get(mode, mode_guidance["CONSISTENCY"])
        source_catalog = [
            {
                "rule_id": rule.get("id"),
                "title": rule.get("title"),
                "source_refs": rule.get("sources", []),
            }
            for rule in structured.get("rules", [])
            if isinstance(rule, dict)
        ]
        payload = await self._invoke_json(
            (
                f"任务类型：{mode}。用户意图：{requested_change}。"
                "只提出一条最重要且可审查的差异建议；不要直接重写整篇文档。"
                "old_text 必须从 markdown 逐字复制一个连续片段，new_text 是替换后的完整片段。"
                "source_refs 只能从 source_catalog 复制；没有可靠来源时返回空数组。"
                "严格返回对象："
                '{"old_text":"原文连续片段","new_text":"替换后的完整片段",'
                '"explanation":"修改原因","source_refs":[]}。'
            ),
            json.dumps(
                {
                    "revision": revision,
                    "markdown": markdown,
                    "known_conflicts": structured.get("conflicts", []),
                    "source_catalog": source_catalog,
                },
                ensure_ascii=False,
            ),
        )
        if not isinstance(payload, dict) or not str(payload.get("old_text", "")):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型建议结构无效。", status=422, retryable=True)
        old_text = str(payload["old_text"])
        if old_text not in markdown:
            raise WorkbenchError("MODEL_JSON_INVALID", "模型建议未能精确引用当前文档。", status=422, retryable=True)
        return {
            "base_revision": revision,
            "old_text": old_text,
            "new_text": str(payload.get("new_text", old_text)),
            "explanation": str(payload.get("explanation", "补充规则完整性")),
            "source_refs": payload.get("source_refs", []) if isinstance(payload.get("source_refs"), list) else [],
        }

    async def generate_qa(self, structured: dict[str, Any]) -> list[dict[str, Any]]:
        payload = await self._invoke_json(
            (
                "基于规则生成不超过 20 条问答，答案必须可由规则支持并保留来源。返回对象："
                '{"items":[{"question":"...","answer":"...","source_refs":[...]}]}。'
            ),
            json.dumps(structured, ensure_ascii=False),
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型 QA 结构无效。", status=422, retryable=True)
        return [
            {
                "question": str(item.get("question", "")),
                "answer": str(item.get("answer", "")),
                "source_refs": item.get("source_refs", []),
            }
            for item in items[:20]
            if isinstance(item, dict) and item.get("question") and item.get("answer")
        ]

    async def generate_evaluation(
        self,
        structured: dict[str, Any],
        qa_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        payload = await self._invoke_json(
            (
                "直接基于规则和 QA 构造最多 8 条简洁的边界或异常场景评测样本，不要解释过程，"
                "不要简单复述问题，每个 input 和 expected 不超过 200 字。返回对象："
                '{"items":[{"input":"...","expected":"...","source_refs":[...]}]}。'
            ),
            json.dumps(
                {
                    "rules": structured.get("rules", [])[:8],
                    "qa_items": qa_items[:8],
                },
                ensure_ascii=False,
            ),
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型评测集结构无效。", status=422, retryable=True)
        normalized = [
            {
                "input": str(item.get("input", "")),
                "expected": str(item.get("expected", "")),
                "source_refs": item.get("source_refs", []),
                "synthetic": True,
                "evaluation_status": "待评测",
            }
            for item in items[:20]
            if isinstance(item, dict) and item.get("input") and item.get("expected")
        ]
        if not normalized:
            raise WorkbenchError("EVALUATION_RESULT_EMPTY", "模型未返回可用评测样本。", status=422, retryable=True)
        return normalized

    @staticmethod
    def _business_context(structured: dict[str, Any]) -> dict[str, Any]:
        return {
            "scene": structured.get("scene", ""),
            "rules": [
                {
                    "id": rule.get("id"),
                    "title": rule.get("title"),
                    "condition": rule.get("condition"),
                    "action": rule.get("action"),
                    "exceptions": rule.get("exceptions"),
                }
                for rule in structured.get("rules", [])[:30]
                if isinstance(rule, dict)
            ],
            "process": structured.get("process", [])[:20],
            "conflicts": structured.get("conflicts", [])[:10],
        }

    async def run_business_case(
        self,
        structured: dict[str, Any],
        input_text: str,
        *,
        expected: str = "",
    ) -> dict[str, Any]:
        evaluation_instruction = (
            "expected 是专家标准答案。请对 answer 与 expected 做语义比对，并返回 correct 与 mismatch_reason。"
            if expected
            else "这是探索性试跑，不要返回虚假的准确率；correct 返回 null。"
        )
        payload = await self._invoke_json(
            (
                "使用给定业务 Skill 的规则与流程处理 input。只给出可审计的业务结论，不输出隐藏思维过程。"
                f"{evaluation_instruction} 严格返回对象："
                '{"answer":"业务结论","verdict":"短标签或结论摘要","confidence":0.0,'
                '"reason":"判断理由","matched_rules":["R-001"],"decision_path":["步骤说明"],'
                '"review_required":false,"correct":null,"mismatch_reason":""}。'
            ),
            json.dumps(
                {
                    "skill": self._business_context(structured),
                    "input": input_text[:20000],
                    "expected": expected[:4000],
                },
                ensure_ascii=False,
            ),
        )
        if not isinstance(payload, dict) or not str(payload.get("answer", "")).strip():
            raise WorkbenchError("MODEL_JSON_INVALID", "模型业务运行结果结构无效。", status=422, retryable=True)
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence > 1:
            confidence /= 100
        matched_rules = payload.get("matched_rules", [])
        decision_path = payload.get("decision_path", [])
        return {
            "answer": str(payload["answer"])[:8000],
            "verdict": str(payload.get("verdict", ""))[:200],
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(payload.get("reason", ""))[:4000],
            "matched_rules": [str(item)[:240] for item in matched_rules[:20]] if isinstance(matched_rules, list) else [],
            "decision_path": [str(item)[:500] for item in decision_path[:20]] if isinstance(decision_path, list) else [],
            "review_required": bool(payload.get("review_required", False)),
            "correct": bool(payload.get("correct")) if expected else None,
            "mismatch_reason": str(payload.get("mismatch_reason", ""))[:2000],
        }

    async def analyze_feedback_case(
        self,
        structured: dict[str, Any],
        case: dict[str, Any],
        *,
        task_type: str,
    ) -> dict[str, Any]:
        if task_type == "GENERATION":
            schema = (
                '{"issues":[{"type":"遗漏要点","description":"..."}],'
                '"expected_content":"应有写法","knowledge_gap":"需补齐的知识","attribution":"遗漏要点"}'
            )
            guidance = "分析生成内容的问题点、应有写法、知识缺口与主要归因。"
        else:
            schema = (
                '{"correct_label":"正确标签","error_reason":"为什么判错",'
                '"correct_reason":"正确判断依据","attribution":"规则阈值","knowledge_gap":"应补齐或修正的规则"}'
            )
            guidance = "分析判别结果的正确标签、错因、正确原因、归因与知识缺口。"
        payload = await self._invoke_json(
            (
                "你是内置错例分析与回流能力。业务 Skill 只提供业务口径，当前任务只形成可供专家修订的初判。"
                f"{guidance} 严格返回：{schema}。"
            ),
            json.dumps(
                {
                    "skill": self._business_context(structured),
                    "case": {
                        "input": str(case.get("input", ""))[:20000],
                        "original_output": str(case.get("original_output", ""))[:10000],
                        "expected": str(case.get("expected", ""))[:4000],
                    },
                },
                ensure_ascii=False,
            ),
        )
        if not isinstance(payload, dict):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型错例分析结果结构无效。", status=422, retryable=True)
        if task_type == "GENERATION":
            issues = payload.get("issues", [])
            return {
                "issues": [
                    {
                        "type": str(item.get("type", "其他"))[:80],
                        "description": str(item.get("description", ""))[:1000],
                    }
                    for item in issues[:20]
                    if isinstance(item, dict) and item.get("description")
                ]
                if isinstance(issues, list)
                else [],
                "expected_content": str(payload.get("expected_content", ""))[:8000],
                "knowledge_gap": str(payload.get("knowledge_gap", ""))[:4000],
                "attribution": str(payload.get("attribution", "其他"))[:80],
            }
        return {
            "correct_label": str(payload.get("correct_label", ""))[:240],
            "error_reason": str(payload.get("error_reason", ""))[:4000],
            "correct_reason": str(payload.get("correct_reason", ""))[:4000],
            "attribution": str(payload.get("attribution", "其他"))[:80],
            "knowledge_gap": str(payload.get("knowledge_gap", ""))[:4000],
        }

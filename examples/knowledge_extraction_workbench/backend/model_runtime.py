"""Optional openJiuwen Model adapter used when a real provider is mounted."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.foundation.llm import (
    JsonOutputParser,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    UserMessage,
)

from .errors import WorkbenchError
from .pipeline import ChunkRef


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
        self._parser = JsonOutputParser()
        client_config = ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            timeout=90,
            max_retries=1,
        )
        request_config = ModelRequestConfig(model=model_name, temperature=temperature, max_tokens=4096)
        self._model = Model(client_config, request_config)

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
                    output_parser=self._parser,
                    timeout=90,
                )
            except Exception as exc:
                raise WorkbenchError(
                    "MODEL_REQUEST_FAILED",
                    "模型请求失败，请检查连接状态后重试。",
                    status=502,
                    retryable=True,
                    details={"reason": type(exc).__name__},
                ) from exc
            parsed = response.parser_content
            if isinstance(parsed, (dict, list)):
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
        payload = await self._invoke_json(
            (
                "合并、去重规则并检测冲突，不丢失 sources。返回对象："
                '{"rules":[{"title":"...","condition":"...","action":"...","exceptions":"...",'
                '"sources":[...]}],"process":[{"step":1,"name":"...","description":"...","sources":[...]}],'
                '"conflicts":["..."]}。'
            ),
            json.dumps({"scene": scene_name, "mapped": mapped}, ensure_ascii=False),
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
            raise WorkbenchError("MODEL_JSON_INVALID", "模型归并结果结构无效。", status=422, retryable=True)
        rules = []
        for item in payload["rules"][:30]:
            if not isinstance(item, dict) or not str(item.get("action", "")).strip():
                continue
            rules.append(
                {
                    "id": f"R-{len(rules) + 1:03d}",
                    "title": str(item.get("title", f"规则 {len(rules) + 1}"))[:120],
                    "condition": str(item.get("condition", "原文未明确条件"))[:500],
                    "action": str(item["action"])[:1000],
                    "exceptions": str(item.get("exceptions", "原文未明确例外，需业务复核"))[:500],
                    "sources": item.get("sources", []),
                }
            )
        if not rules:
            raise WorkbenchError("EXTRACTION_RESULT_EMPTY", "模型未返回可用规则。", status=422, retryable=True)
        process = payload.get("process") if isinstance(payload.get("process"), list) else []
        return {
            "schema_version": "1.0",
            "scene": scene_name,
            "rules": rules,
            "process": process[:20],
            "conflicts": payload.get("conflicts", []) if isinstance(payload.get("conflicts"), list) else [],
            "generated_by": self.model_id,
        }

    async def suggest(self, markdown: str, structured: dict[str, Any], revision: int) -> dict[str, Any]:
        payload = await self._invoke_json(
            (
                "提出一条最重要且可审查的差异建议。old_text 必须逐字存在于 markdown。返回对象："
                '{"old_text":"...","new_text":"...","explanation":"...","source_refs":[...]}。'
            ),
            json.dumps({"markdown": markdown, "structured": structured, "revision": revision}, ensure_ascii=False),
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

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "examples"))

from knowledge_extraction_workbench.backend.errors import WorkbenchError
from knowledge_extraction_workbench.backend.model_runtime import OpenJiuwenKnowledgeModel


def test_decode_json_repairs_common_model_format_errors():
    parsed = OpenJiuwenKnowledgeModel._decode_json(
        "分析完成。```json\n{'old_text':'原文','new_text':'新文','explanation':'原因',}\n```"
    )

    assert parsed == {"old_text": "原文", "new_text": "新文", "explanation": "原因"}


def test_decode_json_accepts_text_content_blocks():
    parsed = OpenJiuwenKnowledgeModel._decode_json([{"type": "text", "text": '{"old_text":"原文","new_text":"新文"}'}])

    assert parsed == {"old_text": "原文", "new_text": "新文"}


def test_decode_json_ignores_minimax_thinking_json_before_final_answer():
    parsed = OpenJiuwenKnowledgeModel._decode_json(
        '<think>先检查 {"message":"这不是最终答案"}</think>\n'
        '{"items":[{"question":"何时复核？","answer":"命中异常时。","source_refs":[]}]}'
    )

    assert parsed == {
        "items": [
            {
                "question": "何时复核？",
                "answer": "命中异常时。",
                "source_refs": [],
            }
        ]
    }


@pytest.mark.parametrize(
    "payload",
    [
        [{"question": "问题", "answer": "答案", "source_refs": [{"material_id": "m-1"}]}],
        {"qa_pairs": [{"query": "问题", "response": "答案", "sources": [{"material_id": "m-1"}]}]},
        {"data": {"items": [{"问题": "问题", "答案": "答案", "来源": [{"material_id": "m-1"}]}]}},
    ],
)
def test_normalize_qa_items_accepts_common_openai_compatible_shapes(payload):
    assert OpenJiuwenKnowledgeModel._normalize_qa_items(payload) == [
        {
            "question": "问题",
            "answer": "答案",
            "source_refs": [{"material_id": "m-1"}],
        }
    ]


def test_normalize_evaluation_items_accepts_wrapped_cases_and_aliases():
    payload = {
        "result": {
            "test_cases": [
                {
                    "scenario": "异常场景",
                    "reference_answer": "人工复核",
                    "citations": [{"material_id": "m-1"}],
                }
            ]
        }
    }

    assert OpenJiuwenKnowledgeModel._normalize_evaluation_items(payload) == [
        {
            "input": "异常场景",
            "expected": "人工复核",
            "source_refs": [{"material_id": "m-1"}],
            "synthetic": True,
            "evaluation_status": "待评测",
        }
    ]


class _StubModel:
    def __init__(self, responses: list[object]):
        self.responses = responses
        self.calls: list[list[object]] = []

    async def invoke(self, messages, **_kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content=self.responses.pop(0))


def _runtime_with_responses(responses: list[object]) -> tuple[OpenJiuwenKnowledgeModel, _StubModel]:
    runtime = object.__new__(OpenJiuwenKnowledgeModel)
    runtime._model_name = "MiniMax-M2.7"
    runtime._temperature = 0.1
    model = _StubModel(responses)
    runtime._model = model
    return runtime, model


@pytest.mark.asyncio
async def test_generate_qa_retries_parseable_but_invalid_schema():
    runtime, model = _runtime_with_responses(
        [
            '{"message":"已生成问答"}',
            '{"items":[{"question":"何时复核？","answer":"命中异常时。","source_refs":[]}]}',
        ]
    )

    result = await runtime.generate_qa({"rules": []})

    assert result[0]["question"] == "何时复核？"
    assert len(model.calls) == 2
    assert "顶层对象字段=['message']" in model.calls[1][-1].content


@pytest.mark.asyncio
async def test_generate_qa_reports_invalid_schema_after_repair_retry():
    runtime, model = _runtime_with_responses(['{"message":"第一次"}', '{"status":"第二次"}'])

    with pytest.raises(WorkbenchError) as caught:
        await runtime.generate_qa({"rules": []})

    assert caught.value.code == "MODEL_JSON_INVALID"
    assert caught.value.message == "模型连续两次未返回可用 QA 结构。"
    assert caught.value.retryable is True
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_generate_qa_falls_back_to_reviewed_rules_after_invalid_model_output():
    runtime, model = _runtime_with_responses(['{"message":"第一次"}', '{"status":"第二次"}'])
    structured = {
        "rules": [
            {
                "title": "异常复核",
                "condition": "命中风险标记",
                "action": "转人工复核",
                "exceptions": "白名单客户除外",
                "sources": [{"material_id": "m-1", "chunk_index": 2}],
            }
        ]
    }

    result = await runtime.generate_qa(structured)

    assert result == [
        {
            "question": "规则“异常复核”的适用条件和处理要求是什么？",
            "answer": "适用条件：命中风险标记；处理要求：转人工复核；例外说明：白名单客户除外。",
            "source_refs": [{"material_id": "m-1", "chunk_index": 2}],
        }
    ]
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_generate_evaluation_falls_back_to_reviewed_rules_after_invalid_model_output():
    runtime, model = _runtime_with_responses(['{"message":"第一次"}', '{"status":"第二次"}'])
    structured = {
        "rules": [
            {
                "title": "异常复核",
                "condition": "命中风险标记",
                "action": "转人工复核",
                "sources": [{"material_id": "m-1", "chunk_index": 2}],
            }
        ]
    }

    result = await runtime.generate_evaluation(structured, [])

    assert result == [
        {
            "input": "业务场景符合“命中风险标记”，应如何处理？",
            "expected": "应执行：转人工复核。",
            "source_refs": [{"material_id": "m-1", "chunk_index": 2}],
            "synthetic": True,
            "evaluation_status": "待评测",
        }
    ]
    assert len(model.calls) == 2

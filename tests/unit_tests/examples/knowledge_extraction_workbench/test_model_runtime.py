from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "examples"))

from knowledge_extraction_workbench.backend.model_runtime import OpenJiuwenKnowledgeModel


def test_decode_json_repairs_common_model_format_errors():
    parsed = OpenJiuwenKnowledgeModel._decode_json(
        "分析完成。```json\n{'old_text':'原文','new_text':'新文','explanation':'原因',}\n```"
    )

    assert parsed == {"old_text": "原文", "new_text": "新文", "explanation": "原因"}


def test_decode_json_accepts_text_content_blocks():
    parsed = OpenJiuwenKnowledgeModel._decode_json([{"type": "text", "text": '{"old_text":"原文","new_text":"新文"}'}])

    assert parsed == {"old_text": "原文", "new_text": "新文"}

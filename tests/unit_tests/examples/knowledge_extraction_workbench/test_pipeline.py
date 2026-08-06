from __future__ import annotations

import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "examples"))

from knowledge_extraction_workbench.backend.config import SecretBox
from knowledge_extraction_workbench.backend.errors import WorkbenchError
from knowledge_extraction_workbench.backend.pipeline import (
    ChunkRef,
    chunk_refs_to_json,
    fair_select_chunks,
    validate_skill_zip,
)


def test_secret_box_encrypts_and_uses_private_key_file(tmp_path):
    key_path = tmp_path / "master.key"
    box = SecretBox(key_path)

    encrypted = box.encrypt("deepseek-test-key")

    assert encrypted != "deepseek-test-key"
    assert "deepseek-test-key" not in encrypted
    assert box.decrypt(encrypted) == "deepseek-test-key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_fair_selection_covers_each_material_and_full_document():
    materials = []
    for material_number in range(3):
        chunks = [
            f"素材{material_number} 第{index}段：" + "这是包含业务规则、审批条件、执行动作和异常处理的有效正文。" * 5
            for index in range(90)
        ]
        materials.append((f"m-{material_number}", f"材料{material_number}.txt", chunks))

    selected = fair_select_chunks(materials, max_total=12)

    assert len(selected) == 12
    for material_number in range(3):
        indexes = [item.chunk_index for item in selected if item.material_id == f"m-{material_number}"]
        assert len(indexes) == 4
        assert min(indexes) < 25
        assert max(indexes) >= 67


def test_fair_selection_rejects_only_short_fragments():
    with pytest.raises(WorkbenchError) as caught:
        fair_select_chunks([("m-1", "empty.txt", ["标题", "一、", "说明"])])
    assert caught.value.code == "MATERIAL_TEXT_EMPTY"


def test_frozen_chunk_snapshot_does_not_contain_material_text():
    chunk = ChunkRef("material-1", "policy.txt", 7, "敏感素材正文" * 20, 2.5)

    snapshot = chunk_refs_to_json([chunk])

    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "敏感素材正文" not in serialized
    assert snapshot[0]["text_sha256"]
    assert snapshot[0]["chunk_index"] == 7


def test_skill_zip_requires_safe_openjiuwen_layout(tmp_path):
    valid = tmp_path / "valid.zip"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: policy-review\ndescription: Review policy rules.\n---\n")
        archive.writestr("references/knowledge.md", "# Knowledge")
        archive.writestr("scripts/validate.py", "print('ok')")

    manifest = validate_skill_zip(valid)

    assert manifest["name"] == "policy-review"
    assert "SKILL.md" in manifest["files"]


def test_skill_zip_rejects_path_traversal(tmp_path):
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: unsafe\ndescription: Unsafe package.\n---\n")
        archive.writestr("../outside.txt", "escape")

    with pytest.raises(WorkbenchError) as caught:
        validate_skill_zip(unsafe)

    assert caught.value.code == "SKILL_ZIP_PATH_INVALID"

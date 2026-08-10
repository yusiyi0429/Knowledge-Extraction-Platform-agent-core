"""Parsing, fair chunk selection, test doubles, and asset generation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from charset_normalizer import from_bytes
from openpyxl import Workbook

from openjiuwen.core.retrieval import CharChunker
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser

from .errors import WorkbenchError

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".txt", ".md"}
TEXT_EXTENSIONS = {".csv", ".tsv", ".txt", ".md"}


@dataclass(frozen=True)
class ChunkRef:
    material_id: str
    material_name: str
    chunk_index: int
    text: str
    score: float

    def source_ref(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "material_name": self.material_name,
            "chunk_index": self.chunk_index,
            "quote": self.text[:160],
        }


@dataclass(frozen=True)
class AssetSpec:
    kind: str
    filename: str
    path: Path
    mime_type: str
    synthetic: bool = False


def _information_score(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    unique_ratio = len(set(compact)) / len(compact)
    punctuation = len(re.findall(r"[，。；：、,.!?！？:;]", text))
    informative = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
    return min(len(compact), 1200) / 1200 + unique_ratio + min(punctuation, 20) / 20 + informative / len(compact)


def normalize_chunk_text(text: str) -> str:
    """Use one canonical representation for selection, hashing, and execution."""
    return text.strip()


def chunk_text_sha256(text: str) -> str:
    return hashlib.sha256(normalize_chunk_text(text).encode()).hexdigest()


def _coverage_best(chunks: list[tuple[int, str]], count: int) -> list[tuple[int, str, float]]:
    """Pick the highest-information chunk from evenly spaced full-document buckets."""
    if count <= 0 or not chunks:
        return []
    count = min(count, len(chunks))
    selected: list[tuple[int, str, float]] = []
    for bucket in range(count):
        start = math.floor(bucket * len(chunks) / count)
        end = math.floor((bucket + 1) * len(chunks) / count)
        candidates = chunks[start : max(start + 1, end)]
        index, text = max(candidates, key=lambda item: (_information_score(item[1]), len(item[1])))
        selected.append((index, text, _information_score(text)))
    return selected


def fair_select_chunks(
    materials: list[tuple[str, str, list[str]]],
    *,
    max_total: int = 24,
    min_chars: int = 80,
) -> list[ChunkRef]:
    """Allocate slots round-robin, then cover each selected material from start to end."""
    usable: list[tuple[str, str, list[tuple[int, str]]]] = []
    for material_id, material_name, chunks in materials:
        filtered = []
        for index, text in enumerate(chunks):
            normalized = normalize_chunk_text(text)
            if len(normalized) >= min_chars:
                filtered.append((index, normalized))
        if filtered:
            usable.append((material_id, material_name, filtered))
    if not usable:
        raise WorkbenchError(
            "MATERIAL_TEXT_EMPTY",
            "素材解析成功，但没有可用于分析的有效正文。",
            status=422,
            retryable=False,
        )

    allocations = [0] * len(usable)
    remaining = [len(item[2]) for item in usable]
    allocated = 0
    while allocated < max_total and any(remaining[index] > allocations[index] for index in range(len(usable))):
        for index in range(len(usable)):
            if allocated >= max_total:
                break
            if allocations[index] < remaining[index]:
                allocations[index] += 1
                allocated += 1

    per_material: list[list[ChunkRef]] = []
    for (material_id, material_name, chunks), count in zip(usable, allocations, strict=True):
        selections = _coverage_best(chunks, count)
        per_material.append(
            [
                ChunkRef(
                    material_id=material_id,
                    material_name=material_name,
                    chunk_index=chunk_index,
                    text=text,
                    score=score,
                )
                for chunk_index, text, score in selections
            ]
        )

    interleaved: list[ChunkRef] = []
    for offset in range(max(len(items) for items in per_material)):
        for items in per_material:
            if offset < len(items):
                interleaved.append(items[offset])
    return interleaved[:max_total]


async def parse_material(path: Path, material_id: str) -> str:
    """Parse supported files through agent-core, with text-table handling for TSV/CSV."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise WorkbenchError(
            "MATERIAL_FORMAT_UNSUPPORTED",
            f"不支持 {suffix or '无扩展名'} 文件，请上传 PDF、DOCX、XLSX、CSV、TSV、TXT 或 MD。",
            status=415,
        )
    if suffix in TEXT_EXTENSIONS:
        detected = from_bytes(path.read_bytes()).best()
        text = str(detected) if detected is not None else ""
    else:
        try:
            documents = await AutoFileParser().parse(str(path), doc_id=material_id, file_name=path.name)
        except Exception as exc:
            raise WorkbenchError(
                "MATERIAL_PARSE_FAILED",
                "素材解析失败，请确认文件未损坏且格式受支持。",
                status=422,
                retryable=False,
                details={"extension": suffix, "reason": type(exc).__name__},
            ) from exc
        text = "\n\n".join(document.text.strip() for document in documents if document.text.strip())
    if not text.strip():
        raise WorkbenchError("MATERIAL_TEXT_EMPTY", "素材中未解析出正文。", status=422)
    return text.strip()


def chunk_material(text: str, *, chunk_size: int = 1200, chunk_overlap: int = 120) -> list[str]:
    return CharChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap).chunk_text(text)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    return [re.sub(r"\s+", " ", part).strip() for part in parts if len(re.sub(r"\s+", "", part)) >= 18]


def _short_title(text: str, fallback: str) -> str:
    cleaned = re.sub(r"^[一二三四五六七八九十0-9.、（）()\-\s]+", "", text)
    cleaned = re.sub(r"[，。；：,:;].*$", "", cleaned).strip()
    return (cleaned[:22] or fallback).rstrip("的与和")


class DeterministicTestModel:
    """Deterministic model injected explicitly by automated tests only."""

    model_id = "deterministic-test-model"

    async def explore(self, chunks: list[ChunkRef]) -> list[dict[str, Any]]:
        sentences: list[tuple[str, ChunkRef]] = []
        for chunk in chunks:
            sentences.extend((sentence, chunk) for sentence in _sentences(chunk.text)[:3])
        if not sentences:
            raise WorkbenchError(
                "EXPLORATION_CANDIDATES_EMPTY",
                "未识别出候选场景，请补充包含业务目标、规则或流程的素材。",
                status=422,
            )
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, (sentence, chunk) in enumerate(sentences):
            title = _short_title(sentence, f"知识场景 {index + 1}")
            normalized = re.sub(r"\W", "", title)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                {
                    "name": title,
                    "description": sentence[:180],
                    "goal": f"沉淀“{title}”中的可执行规则、业务流程与例外处理。",
                    "confidence": round(max(0.58, 0.9 - len(candidates) * 0.08), 2),
                    "source_refs": [chunk.source_ref()],
                }
            )
            if len(candidates) == 3:
                break
        if not candidates:
            raise WorkbenchError("EXPLORATION_CANDIDATES_EMPTY", "模型未返回可用候选场景。", status=422)
        return candidates

    async def map_chunk(self, chunk: ChunkRef, sequence: int) -> dict[str, Any]:
        sentences = _sentences(chunk.text)
        if not sentences:
            sentences = [chunk.text[:240]]
        rules = []
        for local_index, sentence in enumerate(sentences[:2], start=1):
            title = _short_title(sentence, f"规则 {sequence}-{local_index}")
            if any(marker in sentence for marker in ("如果", "当", "若", "满足")):
                condition = sentence[:120]
            else:
                condition = f"业务进入“{title}”环节"
            rules.append(
                {
                    "title": title,
                    "condition": condition,
                    "action": sentence[:220],
                    "exceptions": "原文未明确例外，需业务复核",
                    "sources": [chunk.source_ref()],
                }
            )
        return {"rules": rules, "source": chunk.source_ref()}

    async def reduce(self, mapped: list[dict[str, Any]], scene_name: str) -> dict[str, Any]:
        rules: list[dict[str, Any]] = []
        signatures: set[str] = set()
        for result in mapped:
            for rule in result["rules"]:
                signature = re.sub(r"\W", "", rule["action"]).lower()[:80]
                if not signature or signature in signatures:
                    continue
                signatures.add(signature)
                rule = dict(rule)
                rule["id"] = f"R-{len(rules) + 1:03d}"
                rules.append(rule)
                if len(rules) >= 18:
                    break
        if not rules:
            raise WorkbenchError("EXTRACTION_RESULT_EMPTY", "萃取结果为空，请检查素材正文。", status=422)
        process = [
            {
                "step": index,
                "name": rule["title"],
                "description": rule["action"],
                "sources": rule["sources"],
            }
            for index, rule in enumerate(rules[:8], start=1)
        ]
        return {
            "schema_version": "1.0",
            "scene": scene_name,
            "rules": rules,
            "process": process,
            "conflicts": [],
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
        rules = structured.get("rules", [])
        if rules:
            rule = rules[0]
            old_text = f"- 执行动作：{rule['action']}"
            new_text = f"- 执行动作：{rule['action']}\n- 复核要求：执行前记录依据，异常情况转人工确认。"
            source_refs = rule.get("sources", [])
        else:
            lines = [line for line in markdown.splitlines() if line.strip() and not line.startswith("#")]
            old_text = lines[0] if lines else markdown[:80]
            new_text = old_text + "\n- 复核要求：记录来源并在异常时转人工确认。"
            source_refs = []
        mode_labels = {
            "CONSISTENCY": "一致性检查",
            "REGULATORY": "监管对齐",
            "GAP": "查漏补缺",
            "CUSTOM": "按意图改写",
        }
        intent = f"；已按指令“{instruction[:80]}”定位修改" if instruction else ""
        return {
            "base_revision": revision,
            "old_text": old_text,
            "new_text": new_text,
            "explanation": (f"{mode_labels.get(mode, '一致性检查')}发现执行动作缺少留痕与异常升级条件{intent}。"),
            "source_refs": source_refs,
        }

    async def generate_qa(self, structured: dict[str, Any]) -> list[dict[str, Any]]:
        items = []
        for rule in structured.get("rules", [])[:10]:
            items.append(
                {
                    "question": f"在什么情况下需要执行“{rule['title']}”？",
                    "answer": rule["action"],
                    "source_refs": rule.get("sources", []),
                }
            )
        return items

    async def generate_evaluation(
        self,
        structured: dict[str, Any],
        qa_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        items = []
        for rule in structured.get("rules", [])[:10]:
            items.append(
                {
                    "input": f"请判断以下场景是否需要执行“{rule['title']}”，并说明依据。",
                    "expected": rule["action"],
                    "source_refs": rule.get("sources", []),
                    "synthetic": True,
                    "evaluation_status": "待评测",
                }
            )
        return items or [
            {
                "input": item["question"],
                "expected": item["answer"],
                "source_refs": item.get("source_refs", []),
                "synthetic": True,
                "evaluation_status": "待评测",
            }
            for item in qa_items[:10]
        ]

    async def run_business_case(
        self,
        structured: dict[str, Any],
        input_text: str,
        *,
        expected: str = "",
    ) -> dict[str, Any]:
        rules = structured.get("rules", [])
        matched = rules[0] if rules else {}
        answer = str(matched.get("action") or input_text[:160] or "未找到可执行结论")
        correct = expected.lower() in answer.lower() or answer.lower() in expected.lower() if expected else None
        return {
            "answer": answer,
            "verdict": str(matched.get("title", "规则判断")),
            "confidence": 0.86,
            "reason": "依据已发布 Skill 中的首条可用规则形成确定性测试结论。",
            "matched_rules": [str(matched.get("id", "R-001"))],
            "decision_path": [str(matched.get("title", "读取规则")), "形成业务结论"],
            "review_required": False,
            "correct": correct,
            "mismatch_reason": "输出与标准答案不一致。" if expected and not correct else "",
        }

    async def analyze_feedback_case(
        self,
        structured: dict[str, Any],
        case: dict[str, Any],
        *,
        task_type: str,
    ) -> dict[str, Any]:
        if task_type == "GENERATION":
            return {
                "issues": [{"type": "遗漏要点", "description": "原输出未完整覆盖业务规则与例外条件。"}],
                "expected_content": str(case.get("expected") or "按已发布规则补齐依据与例外条件。"),
                "knowledge_gap": "补充边界条件和人工复核分支。",
                "attribution": "遗漏要点",
            }
        return {
            "correct_label": str(case.get("expected") or "待专家确认"),
            "error_reason": "原输出与专家标准答案或已发布规则不一致。",
            "correct_reason": "应以已发布规则、例外条件和来源证据为准。",
            "attribution": "规则缺失",
            "knowledge_gap": "补充该错例对应的边界规则与正确判断依据。",
        }


def render_markdown(structured: dict[str, Any]) -> str:
    lines = [
        f"# {structured.get('scene', '知识场景')} · 知识研判文档",
        "",
        "> 本文档由受控 Map/Reduce 萃取生成。每条规则保留素材与片段位置，发布前请完成业务复核。",
        "",
        "## 规则清单",
        "",
    ]
    for rule in structured.get("rules", []):
        lines.extend(
            [
                f"### {rule['id']} · {rule['title']}",
                f"- 适用条件：{rule['condition']}",
                f"- 执行动作：{rule['action']}",
                f"- 例外说明：{rule['exceptions']}",
                "- 来源："
                + "；".join(
                    f"{source['material_name']} / 片段 {source['chunk_index'] + 1}"
                    for source in rule.get("sources", [])
                ),
                "",
            ]
        )
    lines.extend(["## 业务流程", ""])
    for node in structured.get("process", []):
        lines.append(f"{node['step']}. **{node['name']}** — {node['description']}")
    lines.extend(["", "## 冲突与待确认项", ""])
    conflicts = structured.get("conflicts", [])
    if conflicts:
        lines.extend(f"- {item}" for item in conflicts)
    else:
        lines.append("- 当前未检测到直接冲突；仍需业务负责人复核例外条件。")
    return "\n".join(lines).strip() + "\n"


def _write_rules_xlsx(path: Path, structured: dict[str, Any]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "规则清单"
    sheet.append(["规则ID", "规则名称", "适用条件", "执行动作", "例外说明", "来源"])
    for rule in structured.get("rules", []):
        sources = "; ".join(
            f"{source['material_name']}#{source['chunk_index'] + 1}" for source in rule.get("sources", [])
        )
        sheet.append([rule["id"], rule["title"], rule["condition"], rule["action"], rule["exceptions"], sources])
    sheet.freeze_panes = "A2"
    widths = (14, 24, 42, 56, 34, 34)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    workbook.save(path)


def _write_skill_zip(path: Path, scene_name: str, markdown: str, structured: dict[str, Any]) -> None:
    safe_name = re.sub(r"[^a-z0-9-]+", "-", scene_name.lower()).strip("-") or "knowledge-scene"
    skill_md = (
        "---\n"
        f"name: {safe_name}\n"
        f"description: Apply reviewed knowledge for {scene_name}.\n"
        "---\n\n"
        f"# {scene_name}\n\n"
        "Use `references/knowledge.md` as the reviewed source of truth.\n"
        "Run `scripts/validate.py` to validate the normalized rule asset before use.\n"
    )
    validator = (
        "import json\n"
        "from pathlib import Path\n\n"
        "payload = json.loads((Path(__file__).parents[1] / 'assets' / 'rules.json').read_text('utf-8'))\n"
        "assert payload.get('schema_version') == '1.0'\n"
        "assert payload.get('rules')\n"
        "print(f\"validated {len(payload['rules'])} rules\")\n"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", skill_md)
        archive.writestr("references/knowledge.md", markdown)
        archive.writestr("assets/rules.json", json.dumps(structured, ensure_ascii=False, indent=2))
        archive.writestr("scripts/validate.py", validator)


async def generate_assets(
    output_dir: Path,
    scene_name: str,
    markdown: str,
    structured: dict[str, Any],
    qa_model: Any,
    evaluation_model: Any,
) -> list[AssetSpec]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rules_path = output_dir / "knowledge-rules.xlsx"
    thought_path = output_dir / "decision-rationale.md"
    skill_path = output_dir / "knowledge-skill.zip"
    qa_path = output_dir / "qa-dataset.jsonl"
    eval_path = output_dir / "synthetic-evaluation.jsonl"

    _write_rules_xlsx(rules_path, structured)
    rationale_lines = [f"# {scene_name} · 决策研判链", "", "该资产记录可审计的业务判断步骤，不包含模型隐藏思维过程。", ""]
    for node in structured.get("process", []):
        rationale_lines.append(f"{node['step']}. {node['name']}：{node['description']}")
    thought_path.write_text("\n".join(rationale_lines) + "\n", encoding="utf-8")
    _write_skill_zip(skill_path, scene_name, markdown, structured)

    qa_items = await qa_model.generate_qa(structured)
    qa_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in qa_items),
        encoding="utf-8",
    )
    eval_items = await evaluation_model.generate_evaluation(structured, qa_items)
    eval_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in eval_items),
        encoding="utf-8",
    )
    return [
        AssetSpec("RULES_XLSX", rules_path.name, rules_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        AssetSpec("THOUGHT_CHAIN_MD", thought_path.name, thought_path, "text/markdown"),
        AssetSpec("SKILL_ZIP", skill_path.name, skill_path, "application/zip"),
        AssetSpec("QA_JSONL", qa_path.name, qa_path, "application/x-ndjson"),
        AssetSpec("EVAL_JSONL", eval_path.name, eval_path, "application/x-ndjson", synthetic=True),
    ]


def validate_skill_zip(path: Path, *, max_uncompressed_bytes: int = 50 * 1024 * 1024) -> dict[str, Any]:
    """Validate a Skill archive without extracting it."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise WorkbenchError("SKILL_ZIP_INVALID", "Skill 包不是有效的 ZIP 文件。", status=422) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 200:
            raise WorkbenchError("SKILL_ZIP_TOO_LARGE", "Skill 包文件数量超过 200。", status=422)
        total = 0
        names: set[str] = set()
        for info in infos:
            name = info.filename
            pure = PurePosixPath(name)
            if not name or "\\" in name or pure.is_absolute() or ".." in pure.parts or "\x00" in name:
                raise WorkbenchError("SKILL_ZIP_PATH_INVALID", "Skill 包包含不安全路径。", status=422)
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise WorkbenchError("SKILL_ZIP_SYMLINK", "Skill 包不能包含符号链接。", status=422)
            total += info.file_size
            if total > max_uncompressed_bytes:
                raise WorkbenchError("SKILL_ZIP_TOO_LARGE", "Skill 包解压后超过 50 MB。", status=422)
            names.add(name.rstrip("/"))
        if "SKILL.md" not in names:
            raise WorkbenchError("SKILL_MANIFEST_MISSING", "Skill 包根目录缺少 SKILL.md。", status=422)
        manifest_text = archive.read("SKILL.md").decode("utf-8", errors="replace")
    match = re.match(r"^---\s*\n(?P<header>.*?)\n---", manifest_text, flags=re.DOTALL)
    if not match:
        raise WorkbenchError("SKILL_MANIFEST_INVALID", "SKILL.md 缺少 YAML front matter。", status=422)
    header = match.group("header")
    name_match = re.search(r"^name:\s*(.+)$", header, flags=re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+)$", header, flags=re.MULTILINE)
    if not name_match or not description_match:
        raise WorkbenchError("SKILL_MANIFEST_INVALID", "SKILL.md 必须声明 name 和 description。", status=422)
    return {
        "name": name_match.group(1).strip().strip("\"'"),
        "description": description_match.group(1).strip().strip("\"'"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "files": sorted(names),
        "uncompressed_bytes": total,
    }


def chunk_refs_to_json(chunks: list[ChunkRef]) -> list[dict[str, Any]]:
    return [
        {
            "material_id": chunk.material_id,
            "material_name": chunk.material_name,
            "chunk_index": chunk.chunk_index,
            "score": chunk.score,
            "text_sha256": chunk_text_sha256(chunk.text),
        }
        for chunk in chunks
    ]

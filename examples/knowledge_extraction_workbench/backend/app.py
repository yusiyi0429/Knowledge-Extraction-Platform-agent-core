"""aiohttp API and static frontend host for the knowledge extraction workbench."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlparse

import aiofiles
from aiohttp import web
from sqlmodel import func, select

from .config import SecretBox, Settings
from .errors import WorkbenchError, error_middleware
from .models import (
    AbilityMount,
    Asset,
    Exploration,
    ExplorationCandidate,
    ExtractionRound,
    Job,
    KnowledgeDocument,
    Material,
    ModelConnection,
    Revision,
    Scene,
    SkillVersion,
    Suggestion,
    new_id,
    utc_now,
)
from .pipeline import SUPPORTED_EXTENSIONS, parse_material, validate_skill_zip
from .service import REQUIRED_ASSET_KINDS, TERMINAL_JOB_STATUSES, WorkbenchService
from .store import ABILITY_SPECS, DEFAULT_SKILLS, Store

APP_SETTINGS = web.AppKey("settings", Settings)
APP_STORE = web.AppKey("store", Store)
APP_SERVICE = web.AppKey("service", WorkbenchService)
APP_SECRET_BOX = web.AppKey("secret_box", SecretBox)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest) as exc:
        raise WorkbenchError("REQUEST_JSON_INVALID", "请求 JSON 无效。", status=400) from exc
    if not isinstance(payload, dict):
        raise WorkbenchError("REQUEST_JSON_INVALID", "请求内容必须是 JSON 对象。", status=400)
    return payload


def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "phase": job.phase,
        "progress": job.progress,
        "seq": job.seq,
        "message": job.message,
        "scene_id": job.scene_id,
        "round_id": job.round_id,
        "exploration_id": job.exploration_id,
        "error": {
            "code": job.error_code,
            "message": job.error_message,
            "retryable": job.retryable,
        }
        if job.error_code
        else None,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
    }


def _round_dict(round_row: ExtractionRound) -> dict[str, Any]:
    return {
        "id": round_row.id,
        "scene_id": round_row.scene_id,
        "version": round_row.version,
        "status": round_row.status,
        "subscenes": _json_loads(round_row.subscenes_json, []),
        "published_at": _iso(round_row.published_at),
        "created_at": _iso(round_row.created_at),
        "updated_at": _iso(round_row.updated_at),
    }


def _material_dict(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "round_id": material.round_id,
        "exploration_id": material.exploration_id,
        "name": material.name,
        "role": material.role,
        "extension": material.extension,
        "size_bytes": material.size_bytes,
        "sha256": material.sha256,
        "enabled": material.enabled,
        "created_at": _iso(material.created_at),
    }


def _document_dict(document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "round_id": document.round_id,
        "markdown": document.markdown,
        "structured": _json_loads(document.structured_json, {}),
        "revision": document.revision,
        "updated_at": _iso(document.updated_at),
    }


def _suggestion_dict(suggestion: Suggestion) -> dict[str, Any]:
    return {
        "id": suggestion.id,
        "round_id": suggestion.round_id,
        "base_revision": suggestion.base_revision,
        "old_text": suggestion.old_text,
        "new_text": suggestion.new_text,
        "explanation": suggestion.explanation,
        "source_refs": _json_loads(suggestion.source_refs_json, []),
        "status": suggestion.status,
        "created_at": _iso(suggestion.created_at),
        "resolved_at": _iso(suggestion.resolved_at),
    }


def _asset_dict(asset: Asset, current_revision: int | None = None) -> dict[str, Any]:
    path = Path(asset.file_path)
    return {
        "id": asset.id,
        "round_id": asset.round_id,
        "kind": asset.kind,
        "filename": asset.filename,
        "mime_type": asset.mime_type,
        "version": asset.version,
        "source_revision": asset.source_revision,
        "stale": current_revision is not None and asset.source_revision != current_revision,
        "synthetic": asset.synthetic,
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "created_at": _iso(asset.created_at),
        "download_url": f"/api/v1/assets/{asset.id}/download",
    }


def _model_dict(model: ModelConnection) -> dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "api_base": model.api_base,
        "model_name": model.model_name,
        "enabled": model.enabled,
        "has_api_key": bool(model.encrypted_api_key),
        "created_at": _iso(model.created_at),
        "updated_at": _iso(model.updated_at),
    }


def _skill_dict(skill: SkillVersion) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "status": skill.status,
        "built_in": bool(_json_loads(skill.manifest_json, {}).get("built_in")),
        "manifest": _json_loads(skill.manifest_json, {}),
        "created_at": _iso(skill.created_at),
    }


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "mode": "local-demo", "model_runtime": "FakeModel"})


async def api_not_found(_: web.Request) -> web.Response:
    raise WorkbenchError("API_NOT_FOUND", "API 路径不存在。", status=404)


async def dashboard(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    counts = store.dashboard_counts()
    with store.session() as session:
        documents = session.exec(select(KnowledgeDocument)).all()
        counts["rules"] = sum(len(_json_loads(item.structured_json, {}).get("rules", [])) for item in documents)
        running_jobs = session.exec(
            select(func.count()).select_from(Job).where(Job.status.in_(["QUEUED", "RUNNING"]))
        ).one()
    return web.json_response({"metrics": counts | {"running_jobs": int(running_jobs)}, "evaluation_status": "待评测"})


async def list_scenes(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    query_text = request.query.get("q", "").strip().lower()
    status = request.query.get("status", "ALL").upper()
    with store.session() as session:
        statement = select(Scene).where(Scene.archived_at.is_(None)).order_by(Scene.updated_at.desc())
        if status != "ALL":
            statement = statement.where(Scene.status == status)
        rows = session.exec(statement).all()
        items = []
        for scene in rows:
            if query_text and query_text not in f"{scene.name} {scene.description} {scene.goal}".lower():
                continue
            latest = session.exec(
                select(ExtractionRound)
                .where(ExtractionRound.scene_id == scene.id)
                .order_by(ExtractionRound.version.desc())
            ).first()
            material_count = 0
            rule_count = 0
            asset_count = 0
            if latest:
                material_count = int(
                    session.exec(
                        select(func.count()).select_from(Material).where(Material.round_id == latest.id, Material.enabled == True)  # noqa: E712
                    ).one()
                )
                document = session.exec(
                    select(KnowledgeDocument).where(KnowledgeDocument.round_id == latest.id)
                ).first()
                rule_count = len(_json_loads(document.structured_json, {}).get("rules", [])) if document else 0
                asset_count = len(
                    {
                        asset.kind
                        for asset in session.exec(select(Asset).where(Asset.round_id == latest.id)).all()
                    }
                )
            items.append(
                {
                    "id": scene.id,
                    "name": scene.name,
                    "description": scene.description,
                    "goal": scene.goal,
                    "owner": scene.owner,
                    "status": scene.status,
                    "round": _round_dict(latest) if latest else None,
                    "material_count": material_count,
                    "rule_count": rule_count,
                    "asset_count": asset_count,
                    "created_at": _iso(scene.created_at),
                    "updated_at": _iso(scene.updated_at),
                }
            )
    return web.json_response({"items": items})


async def create_scene(request: web.Request) -> web.Response:
    scene, round_row = request.app[APP_SERVICE].create_scene(await _json_body(request))
    return web.json_response({"scene": {"id": scene.id, "name": scene.name}, "round": _round_dict(round_row)}, status=201)


async def get_scene(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    scene_id = request.match_info["scene_id"]
    with store.session() as session:
        scene = session.get(Scene, scene_id)
        if scene is None or scene.archived_at is not None:
            raise WorkbenchError("SCENE_NOT_FOUND", "场景不存在。", status=404)
        rounds = session.exec(
            select(ExtractionRound).where(ExtractionRound.scene_id == scene_id).order_by(ExtractionRound.version.desc())
        ).all()
    return web.json_response(
        {
            "id": scene.id,
            "name": scene.name,
            "description": scene.description,
            "goal": scene.goal,
            "owner": scene.owner,
            "status": scene.status,
            "rounds": [_round_dict(item) for item in rounds],
            "created_at": _iso(scene.created_at),
            "updated_at": _iso(scene.updated_at),
        }
    )


async def update_scene(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    payload = await _json_body(request)
    scene_id = request.match_info["scene_id"]
    with store.session() as session:
        scene = session.get(Scene, scene_id)
        if scene is None or scene.archived_at is not None:
            raise WorkbenchError("SCENE_NOT_FOUND", "场景不存在。", status=404)
        latest = session.exec(
            select(ExtractionRound)
            .where(ExtractionRound.scene_id == scene_id)
            .order_by(ExtractionRound.version.desc())
        ).first()
        if latest and latest.status == "PUBLISHED":
            raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可修改，请创建新一轮。", status=409)
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name:
                raise WorkbenchError("SCENE_NAME_REQUIRED", "请输入场景名称。", status=422)
            scene.name = name[:160]
        if "description" in payload:
            scene.description = str(payload["description"]).strip()
        if "goal" in payload:
            scene.goal = str(payload["goal"]).strip()
        if latest and "subscenes" in payload:
            latest.subscenes_json = json.dumps(payload["subscenes"], ensure_ascii=False)
            latest.updated_at = utc_now()
            session.add(latest)
        scene.updated_at = utc_now()
        session.add(scene)
        session.commit()
    return web.json_response({"id": scene.id, "name": scene.name, "updated_at": _iso(scene.updated_at)})


async def archive_scene(request: web.Request) -> web.Response:
    request.app[APP_SERVICE].archive_scene(request.match_info["scene_id"])
    return web.json_response({"archived": True})


async def list_rounds(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    scene_id = request.match_info["scene_id"]
    store.get(Scene, scene_id, code="SCENE_NOT_FOUND")
    with store.session() as session:
        rounds = session.exec(
            select(ExtractionRound).where(ExtractionRound.scene_id == scene_id).order_by(ExtractionRound.version.desc())
        ).all()
    return web.json_response({"items": [_round_dict(item) for item in rounds]})


async def create_round(request: web.Request) -> web.Response:
    round_row = request.app[APP_SERVICE].create_next_round(request.match_info["scene_id"])
    return web.json_response(_round_dict(round_row), status=201)


async def list_materials(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    round_id = request.match_info["round_id"]
    store.get(ExtractionRound, round_id, code="ROUND_NOT_FOUND")
    with store.session() as session:
        rows = session.exec(select(Material).where(Material.round_id == round_id).order_by(Material.created_at)).all()
    return web.json_response({"items": [_material_dict(item) for item in rows]})


async def _receive_upload(
    request: web.Request,
    *,
    round_id: str | None = None,
    exploration_id: str | None = None,
) -> Material:
    settings = request.app[APP_SETTINGS]
    store = request.app[APP_STORE]
    if round_id:
        round_row = store.get(ExtractionRound, round_id, code="ROUND_NOT_FOUND")
        if round_row.status == "PUBLISHED":
            raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可上传素材。", status=409)
        parent = settings.upload_dir / "rounds" / round_id
    else:
        exploration = store.get(Exploration, str(exploration_id), code="EXPLORATION_NOT_FOUND")
        if exploration.status == "ANALYZING":
            raise WorkbenchError("EXPLORATION_JOB_CONFLICT", "探索正在分析，暂不能上传素材。", status=409)
        parent = settings.upload_dir / "explorations" / str(exploration_id)
    parent.mkdir(parents=True, exist_ok=True)
    try:
        reader = await request.multipart()
    except (AssertionError, web.HTTPBadRequest) as exc:
        raise WorkbenchError("UPLOAD_MULTIPART_REQUIRED", "请使用 multipart/form-data 上传文件。", status=400) from exc
    part = await reader.next()
    while part is not None and not part.filename:
        part = await reader.next()
    if part is None or not part.filename:
        raise WorkbenchError("UPLOAD_FILE_REQUIRED", "请选择要上传的文件。", status=422)
    original_name = part.filename
    safe_name = PurePath(original_name).name
    if not safe_name or safe_name != original_name.replace("\\", "/").split("/")[-1] or ".." in PurePath(safe_name).parts:
        raise WorkbenchError("UPLOAD_FILENAME_INVALID", "文件名不安全。", status=422)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise WorkbenchError(
            "MATERIAL_FORMAT_UNSUPPORTED",
            "仅支持 PDF、DOCX、XLSX、CSV、TSV、TXT 和 MD。",
            status=415,
            details={"extension": suffix},
        )
    material_id = new_id()
    target_dir = parent / material_id
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / safe_name
    partial = target_dir / f"{safe_name}.uploading"
    digest = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(partial, "wb") as stream:
            while chunk := await part.read_chunk(size=1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise WorkbenchError(
                        "MATERIAL_TOO_LARGE",
                        f"单文件不能超过 {settings.max_upload_bytes // (1024 * 1024)} MB。",
                        status=413,
                    )
                digest.update(chunk)
                await stream.write(chunk)
        partial.replace(target)
        text = await parse_material(target, material_id)
        parsed_path = target_dir / "parsed.txt"
        async with aiofiles.open(parsed_path, "w", encoding="utf-8") as stream:
            await stream.write(text)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    material = Material(
        id=material_id,
        round_id=round_id,
        exploration_id=exploration_id,
        name=safe_name,
        role=request.query.get("role", "REFERENCE")[:32],
        file_path=str(target),
        parsed_path=str(parsed_path),
        extension=suffix,
        size_bytes=size,
        sha256=digest.hexdigest(),
    )
    with store.session() as session:
        session.add(material)
        session.commit()
        session.refresh(material)
        session.expunge(material)
    return material


async def upload_round_material(request: web.Request) -> web.Response:
    material = await _receive_upload(request, round_id=request.match_info["round_id"])
    return web.json_response(_material_dict(material), status=201)


async def update_material(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    payload = await _json_body(request)
    material_id = request.match_info["material_id"]
    with store.session() as session:
        material = session.get(Material, material_id)
        if material is None:
            raise WorkbenchError("MATERIAL_NOT_FOUND", "素材不存在。", status=404)
        if material.round_id:
            round_row = session.get(ExtractionRound, material.round_id)
            if round_row and round_row.status == "PUBLISHED":
                raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可修改素材。", status=409)
        if "enabled" in payload:
            material.enabled = bool(payload["enabled"])
        if "role" in payload:
            material.role = str(payload["role"])[:32]
        session.add(material)
        session.commit()
        session.refresh(material)
        session.expunge(material)
    return web.json_response(_material_dict(material))


async def create_exploration(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    exploration = Exploration(
        name=str(payload.get("name", "场景探索")).strip()[:160] or "场景探索",
        goal=str(payload.get("goal", "")).strip(),
    )
    store = request.app[APP_STORE]
    with store.session() as session:
        session.add(exploration)
        session.commit()
        session.refresh(exploration)
        session.expunge(exploration)
    return web.json_response(
        {"id": exploration.id, "name": exploration.name, "goal": exploration.goal, "status": exploration.status},
        status=201,
    )


async def list_explorations(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    with store.session() as session:
        rows = session.exec(
            select(Exploration).where(Exploration.archived_at.is_(None)).order_by(Exploration.updated_at.desc())
        ).all()
        items = []
        for row in rows:
            material_count = session.exec(
                select(func.count()).select_from(Material).where(Material.exploration_id == row.id)
            ).one()
            candidate_count = session.exec(
                select(func.count()).select_from(ExplorationCandidate).where(
                    ExplorationCandidate.exploration_id == row.id
                )
            ).one()
            items.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "goal": row.goal,
                    "status": row.status,
                    "material_count": int(material_count),
                    "candidate_count": int(candidate_count),
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                }
            )
    return web.json_response({"items": items})


async def archive_exploration(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    exploration_id = request.match_info["exploration_id"]
    with store.session() as session:
        exploration = session.get(Exploration, exploration_id)
        if exploration is None or exploration.archived_at is not None:
            raise WorkbenchError("EXPLORATION_NOT_FOUND", "探索记录不存在。", status=404)
        running = session.exec(
            select(Job).where(Job.exploration_id == exploration_id, Job.status.in_(["QUEUED", "RUNNING"]))
        ).first()
        if running:
            raise WorkbenchError(
                "EXPLORATION_JOB_CONFLICT",
                "探索任务运行中，完成或失败后才能归档。",
                status=409,
                retryable=True,
                details={"job_id": running.id},
            )
        exploration.status = "ARCHIVED"
        exploration.archived_at = utc_now()
        exploration.updated_at = utc_now()
        session.add(exploration)
        session.commit()
    return web.json_response({"archived": True})


async def upload_exploration_material(request: web.Request) -> web.Response:
    material = await _receive_upload(request, exploration_id=request.match_info["exploration_id"])
    return web.json_response(_material_dict(material), status=201)


async def analyze_exploration(request: web.Request) -> web.Response:
    job = request.app[APP_SERVICE].start_exploration(request.match_info["exploration_id"])
    return web.json_response(_job_dict(job), status=202)


async def list_exploration_materials(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    exploration_id = request.match_info["exploration_id"]
    store.get(Exploration, exploration_id, code="EXPLORATION_NOT_FOUND")
    with store.session() as session:
        rows = session.exec(
            select(Material).where(Material.exploration_id == exploration_id).order_by(Material.created_at)
        ).all()
    return web.json_response({"items": [_material_dict(item) for item in rows]})


async def list_candidates(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    exploration_id = request.match_info["exploration_id"]
    store.get(Exploration, exploration_id, code="EXPLORATION_NOT_FOUND")
    with store.session() as session:
        rows = session.exec(
            select(ExplorationCandidate)
            .where(ExplorationCandidate.exploration_id == exploration_id)
            .order_by(ExplorationCandidate.confidence.desc())
        ).all()
    return web.json_response(
        {
            "items": [
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "goal": row.goal,
                    "confidence": row.confidence,
                    "source_refs": _json_loads(row.source_refs_json, []),
                    "created_scene_id": row.created_scene_id,
                }
                for row in rows
            ]
        }
    )


async def candidate_create_scene(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    service = request.app[APP_SERVICE]
    exploration_id = request.match_info["exploration_id"]
    candidate_id = request.match_info["candidate_id"]
    with store.session() as session:
        candidate = session.get(ExplorationCandidate, candidate_id)
        if candidate is None or candidate.exploration_id != exploration_id:
            raise WorkbenchError("CANDIDATE_NOT_FOUND", "候选场景不存在。", status=404)
        if candidate.created_scene_id:
            existing_id = candidate.created_scene_id
            return web.json_response({"scene_id": existing_id, "already_created": True})
        materials = session.exec(
            select(Material).where(Material.exploration_id == exploration_id, Material.enabled == True)  # noqa: E712
        ).all()
        for material in materials:
            session.expunge(material)
    scene, round_row = service.create_scene(
        {"name": candidate.name, "description": candidate.description, "goal": candidate.goal}
    )
    with store.session() as session:
        for material in materials:
            session.add(
                Material(
                    round_id=round_row.id,
                    name=material.name,
                    role=material.role,
                    file_path=material.file_path,
                    parsed_path=material.parsed_path,
                    extension=material.extension,
                    size_bytes=material.size_bytes,
                    sha256=material.sha256,
                )
            )
        candidate_row = session.get(ExplorationCandidate, candidate_id)
        if candidate_row:
            candidate_row.created_scene_id = scene.id
            session.add(candidate_row)
        session.commit()
    return web.json_response({"scene_id": scene.id, "round_id": round_row.id}, status=201)


async def start_extraction(request: web.Request) -> web.Response:
    job = request.app[APP_SERVICE].start_extraction(request.match_info["round_id"])
    return web.json_response(_job_dict(job), status=202)


async def get_job(request: web.Request) -> web.Response:
    job = request.app[APP_STORE].get(Job, request.match_info["job_id"], code="JOB_NOT_FOUND")
    return web.json_response(_job_dict(job))


async def job_events(request: web.Request) -> web.StreamResponse:
    store = request.app[APP_STORE]
    job_id = request.match_info["job_id"]
    store.get(Job, job_id, code="JOB_NOT_FOUND")
    raw_last = request.headers.get("Last-Event-ID", request.query.get("after", "0"))
    try:
        last_seq = max(0, int(raw_last))
    except ValueError:
        last_seq = 0
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    try:
        while True:
            rows = store.events_after(job_id, last_seq)
            for row in rows:
                payload = {
                    "seq": row.seq,
                    "phase": row.phase,
                    "status": row.status,
                    "progress": row.progress,
                    "message": row.message,
                }
                await response.write(
                    f"id: {row.seq}\nevent: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                )
                last_seq = row.seq
            job = store.get(Job, job_id, code="JOB_NOT_FOUND")
            if job.status in TERMINAL_JOB_STATUSES and not rows:
                break
            await asyncio.sleep(0.15)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response


async def retry_job(request: web.Request) -> web.Response:
    job = request.app[APP_SERVICE].retry_job(request.match_info["job_id"])
    return web.json_response(_job_dict(job), status=202)


async def get_document(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    round_id = request.match_info["round_id"]
    store.get(ExtractionRound, round_id, code="ROUND_NOT_FOUND")
    with store.session() as session:
        document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
    if document is None:
        raise WorkbenchError("DOCUMENT_NOT_FOUND", "当前轮次尚未生成知识文档。", status=404)
    return web.json_response(_document_dict(document))


async def save_document(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    round_id = request.match_info["round_id"]
    payload = await _json_body(request)
    markdown = str(payload.get("markdown", ""))
    try:
        base_revision = int(payload.get("base_revision"))
    except (TypeError, ValueError) as exc:
        raise WorkbenchError("BASE_REVISION_REQUIRED", "保存时必须提供 base_revision。", status=422) from exc
    with store.session() as session:
        round_row = session.get(ExtractionRound, round_id)
        if round_row is None:
            raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
        if round_row.status == "PUBLISHED":
            raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可修改。", status=409)
        document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
        if document is None:
            raise WorkbenchError("DOCUMENT_NOT_FOUND", "知识文档不存在。", status=404)
        if document.revision != base_revision:
            raise WorkbenchError(
                "DOCUMENT_REVISION_CONFLICT",
                "文档已被更新，请刷新后再保存。",
                status=409,
                details={"current_revision": document.revision},
            )
        document.markdown = markdown
        document.revision += 1
        document.updated_at = utc_now()
        session.add(document)
        session.add(
            Revision(
                document_id=document.id,
                revision=document.revision,
                markdown=markdown,
                reason=str(payload.get("reason", "手工保存"))[:200],
                author="本机用户",
            )
        )
        session.commit()
        session.refresh(document)
        session.expunge(document)
    return web.json_response(_document_dict(document))


async def list_revisions(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    round_id = request.match_info["round_id"]
    with store.session() as session:
        document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
        if document is None:
            return web.json_response({"items": []})
        rows = session.exec(
            select(Revision).where(Revision.document_id == document.id).order_by(Revision.revision.desc())
        ).all()
    return web.json_response(
        {
            "items": [
                {
                    "id": row.id,
                    "revision": row.revision,
                    "reason": row.reason,
                    "author": row.author,
                    "created_at": _iso(row.created_at),
                }
                for row in rows
            ]
        }
    )


async def list_suggestions(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    round_id = request.match_info["round_id"]
    with store.session() as session:
        rows = session.exec(
            select(Suggestion).where(Suggestion.round_id == round_id).order_by(Suggestion.created_at.desc())
        ).all()
    return web.json_response({"items": [_suggestion_dict(item) for item in rows]})


async def create_suggestion(request: web.Request) -> web.Response:
    suggestion = await request.app[APP_SERVICE].create_suggestion(request.match_info["round_id"])
    return web.json_response(_suggestion_dict(suggestion), status=201)


async def apply_suggestion(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    try:
        base_revision = int(payload.get("base_revision"))
    except (TypeError, ValueError) as exc:
        raise WorkbenchError("BASE_REVISION_REQUIRED", "采纳建议时必须提供 base_revision。", status=422) from exc
    document = request.app[APP_SERVICE].apply_suggestion(
        request.match_info["suggestion_id"], expected_revision=base_revision
    )
    return web.json_response(_document_dict(document))


async def reject_suggestion(request: web.Request) -> web.Response:
    suggestion = request.app[APP_SERVICE].reject_suggestion(request.match_info["suggestion_id"])
    return web.json_response(_suggestion_dict(suggestion))


async def list_assets(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    round_id = request.match_info["round_id"]
    store.get(ExtractionRound, round_id, code="ROUND_NOT_FOUND")
    with store.session() as session:
        rows = session.exec(
            select(Asset).where(Asset.round_id == round_id).order_by(Asset.kind, Asset.version.desc())
        ).all()
        document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
    latest: dict[str, Asset] = {}
    for row in rows:
        latest.setdefault(row.kind, row)
    return web.json_response(
        {
            "items": [_asset_dict(item, document.revision if document else None) for item in latest.values()],
            "complete": bool(document)
            and REQUIRED_ASSET_KINDS.issubset(latest.keys())
            and all(item.source_revision == document.revision for item in latest.values()),
        }
    )


async def generate_round_assets(request: web.Request) -> web.Response:
    payload = await _json_body(request) if request.can_read_body else {}
    job = request.app[APP_SERVICE].start_asset_generation(
        request.match_info["round_id"], only_kind=payload.get("kind")
    )
    return web.json_response(_job_dict(job), status=202)


async def download_asset(request: web.Request) -> web.StreamResponse:
    asset = request.app[APP_STORE].get(Asset, request.match_info["asset_id"], code="ASSET_NOT_FOUND")
    path = Path(asset.file_path)
    if not path.is_file():
        raise WorkbenchError("ASSET_FILE_MISSING", "资产文件不存在，请重新生成。", status=404)
    response = web.FileResponse(path)
    response.content_type = asset.mime_type
    response.headers["Content-Disposition"] = f'attachment; filename="{asset.filename}"'
    return response


async def publish_round(request: web.Request) -> web.Response:
    round_row = request.app[APP_SERVICE].publish_round(request.match_info["round_id"])
    return web.json_response(_round_dict(round_row))


def _validate_model_endpoint(provider: str, api_base: str) -> None:
    if provider == "FakeModel":
        return
    parsed = urlparse(api_base)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise WorkbenchError(
        "MODEL_API_BASE_INVALID",
        "API 地址必须使用 HTTPS；仅本机模型允许 HTTP loopback 地址。",
        status=422,
    )


async def list_models(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    with store.session() as session:
        rows = session.exec(select(ModelConnection).order_by(ModelConnection.created_at)).all()
    return web.json_response({"items": [_model_dict(item) for item in rows]})


async def create_model(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    name = str(payload.get("name", "")).strip()
    provider = str(payload.get("provider", "")).strip()
    api_base = str(payload.get("api_base", "")).strip()
    model_name = str(payload.get("model_name", "")).strip()
    api_key = str(payload.get("api_key", "")).strip()
    if not name or not provider or not model_name:
        raise WorkbenchError("MODEL_FIELDS_REQUIRED", "名称、Provider 和模型名称为必填项。", status=422)
    if provider != "FakeModel" and not api_key:
        raise WorkbenchError("MODEL_API_KEY_REQUIRED", "请输入 API Key。", status=422)
    _validate_model_endpoint(provider, api_base)
    model = ModelConnection(
        name=name[:120],
        provider=provider[:40],
        api_base=api_base[:500],
        model_name=model_name[:160],
        encrypted_api_key=request.app[APP_SECRET_BOX].encrypt(api_key),
        enabled=bool(payload.get("enabled", True)),
    )
    store = request.app[APP_STORE]
    with store.session() as session:
        session.add(model)
        session.commit()
        session.refresh(model)
        session.expunge(model)
    return web.json_response(_model_dict(model), status=201)


async def update_model(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    store = request.app[APP_STORE]
    model_id = request.match_info["model_id"]
    with store.session() as session:
        model = session.get(ModelConnection, model_id)
        if model is None:
            raise WorkbenchError("MODEL_NOT_FOUND", "模型连接不存在。", status=404)
        provider = str(payload.get("provider", model.provider)).strip()
        api_base = str(payload.get("api_base", model.api_base)).strip()
        _validate_model_endpoint(provider, api_base)
        for field, limit in (("name", 120), ("provider", 40), ("api_base", 500), ("model_name", 160)):
            if field in payload:
                setattr(model, field, str(payload[field]).strip()[:limit])
        if payload.get("api_key"):
            model.encrypted_api_key = request.app[APP_SECRET_BOX].encrypt(str(payload["api_key"]).strip())
        if "enabled" in payload:
            model.enabled = bool(payload["enabled"])
        model.updated_at = utc_now()
        session.add(model)
        session.commit()
        session.refresh(model)
        session.expunge(model)
    return web.json_response(_model_dict(model))


async def delete_model(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    model_id = request.match_info["model_id"]
    with store.session() as session:
        model = session.get(ModelConnection, model_id)
        if model is None:
            raise WorkbenchError("MODEL_NOT_FOUND", "模型连接不存在。", status=404)
        if model.provider == "FakeModel":
            raise WorkbenchError("DEFAULT_MODEL_REQUIRED", "内置 Fake Model 不能删除。", status=409)
        mount = session.exec(select(AbilityMount).where(AbilityMount.model_connection_id == model_id)).first()
        if mount:
            raise WorkbenchError(
                "MODEL_IN_USE",
                "该模型仍被能力配置使用，请先更换挂载模型。",
                status=409,
                details={"ability_key": mount.ability_key},
            )
        session.delete(model)
        session.commit()
    return web.json_response({"deleted": True})


async def test_model(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    model = store.get(ModelConnection, request.match_info["model_id"], code="MODEL_NOT_FOUND")
    if model.provider == "FakeModel":
        await asyncio.sleep(0.08)
        return web.json_response({"ok": True, "latency_ms": 80, "message": "Fake Model 可用"})
    if not model.encrypted_api_key:
        raise WorkbenchError("MODEL_API_KEY_REQUIRED", "该连接尚未保存 API Key。", status=422)
    _validate_model_endpoint(model.provider, model.api_base)
    started = asyncio.get_running_loop().time()
    try:
        from openjiuwen.core.foundation.llm import Model
        from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

        client = ModelClientConfig(
            client_provider=model.provider,
            api_key=request.app[APP_SECRET_BOX].decrypt(model.encrypted_api_key),
            api_base=model.api_base,
            timeout=20,
            max_retries=0,
        )
        runtime = Model(client, ModelRequestConfig(model=model.model_name, temperature=0, max_tokens=8))
        await runtime.invoke("仅回复 OK", model=model.model_name, timeout=20)
    except Exception as exc:
        raise WorkbenchError(
            "MODEL_REQUEST_FAILED",
            "模型连接测试失败，请检查 Provider、地址、模型名称和密钥。",
            status=502,
            retryable=True,
            details={"reason": type(exc).__name__},
        ) from exc
    latency_ms = round((asyncio.get_running_loop().time() - started) * 1000)
    return web.json_response({"ok": True, "latency_ms": latency_ms, "message": "模型连接可用"})


async def list_skills(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    with store.session() as session:
        rows = session.exec(select(SkillVersion).order_by(SkillVersion.created_at)).all()
    return web.json_response({"items": [_skill_dict(item) for item in rows]})


async def upload_skill(request: web.Request) -> web.Response:
    settings = request.app[APP_SETTINGS]
    try:
        reader = await request.multipart()
    except (AssertionError, web.HTTPBadRequest) as exc:
        raise WorkbenchError("UPLOAD_MULTIPART_REQUIRED", "请上传 ZIP 格式 Skill 包。", status=400) from exc
    part = await reader.next()
    while part is not None and not part.filename:
        part = await reader.next()
    if part is None or not part.filename or Path(part.filename).suffix.lower() != ".zip":
        raise WorkbenchError("SKILL_ZIP_REQUIRED", "请选择 ZIP 格式 Skill 包。", status=422)
    upload_id = new_id()
    temporary = settings.skill_dir / f"{upload_id}.uploading"
    final_path = settings.skill_dir / f"{upload_id}.zip"
    size = 0
    try:
        async with aiofiles.open(temporary, "wb") as stream:
            while chunk := await part.read_chunk(size=512 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise WorkbenchError("SKILL_ZIP_TOO_LARGE", "Skill ZIP 不能超过 20 MB。", status=413)
                await stream.write(chunk)
        manifest = validate_skill_zip(temporary)
        temporary.replace(final_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    skill = SkillVersion(
        name=manifest["name"][:120],
        description=manifest["description"],
        version=str(request.query.get("version", "1.0.0"))[:40],
        package_path=str(final_path),
        manifest_json=json.dumps(manifest, ensure_ascii=False),
    )
    store = request.app[APP_STORE]
    with store.session() as session:
        session.add(skill)
        session.commit()
        session.refresh(skill)
        session.expunge(skill)
    return web.json_response(_skill_dict(skill), status=201)


async def list_ability_mounts(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    with store.session() as session:
        mounts = session.exec(select(AbilityMount).order_by(AbilityMount.display_name)).all()
        models = {item.id: _model_dict(item) for item in session.exec(select(ModelConnection)).all()}
        skills = {item.id: _skill_dict(item) for item in session.exec(select(SkillVersion)).all()}
    return web.json_response(
        {
            "items": [
                {
                    "id": mount.id,
                    "ability_key": mount.ability_key,
                    "display_name": mount.display_name,
                    "description": mount.description,
                    "enabled": mount.enabled,
                    "model_connection_id": mount.model_connection_id,
                    "skill_version_id": mount.skill_version_id,
                    "model": models.get(str(mount.model_connection_id)),
                    "skill": skills.get(str(mount.skill_version_id)),
                    "params": _json_loads(mount.params_json, {}),
                    "updated_at": _iso(mount.updated_at),
                }
                for mount in mounts
            ]
        }
    )


async def update_ability_mount(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    store = request.app[APP_STORE]
    mount_id = request.match_info["mount_id"]
    with store.session() as session:
        mount = session.get(AbilityMount, mount_id)
        if mount is None:
            raise WorkbenchError("ABILITY_MOUNT_NOT_FOUND", "能力配置不存在。", status=404)
        if "model_connection_id" in payload:
            model_id = payload["model_connection_id"] or None
            if model_id and session.get(ModelConnection, model_id) is None:
                raise WorkbenchError("MODEL_NOT_FOUND", "模型连接不存在。", status=404)
            mount.model_connection_id = model_id
        if "skill_version_id" in payload:
            skill_id = payload["skill_version_id"] or None
            if skill_id and session.get(SkillVersion, skill_id) is None:
                raise WorkbenchError("SKILL_NOT_FOUND", "Skill 不存在。", status=404)
            mount.skill_version_id = skill_id
        if "enabled" in payload:
            mount.enabled = bool(payload["enabled"])
        if "params" in payload:
            params = payload["params"]
            if not isinstance(params, dict):
                raise WorkbenchError("ABILITY_PARAMS_INVALID", "能力参数必须是 JSON 对象。", status=422)
            mount.params_json = json.dumps(params, ensure_ascii=False)
        mount.updated_at = utc_now()
        session.add(mount)
        session.commit()
    return web.json_response({"updated": True, "id": mount_id})


async def reset_ability_mounts(request: web.Request) -> web.Response:
    store = request.app[APP_STORE]
    with store.session() as session:
        fake = session.exec(select(ModelConnection).where(ModelConnection.provider == "FakeModel")).first()
        skills = {item.name: item for item in session.exec(select(SkillVersion)).all()}
        skill_order = [skills[item[0]] for item in DEFAULT_SKILLS]
        mounts = {item.ability_key: item for item in session.exec(select(AbilityMount)).all()}
        for index, (key, _, _) in enumerate(ABILITY_SPECS):
            mount = mounts[key]
            mount.enabled = True
            mount.model_connection_id = fake.id if fake else None
            mount.skill_version_id = skill_order[min(index // 2, len(skill_order) - 1)].id
            mount.params_json = json.dumps(
                {"temperature": 0.2, "max_chunks": 24, "concurrency": 3}, ensure_ascii=False
            )
            mount.updated_at = utc_now()
            session.add(mount)
        session.commit()
    return web.json_response({"reset": True})


async def serve_index(request: web.Request) -> web.StreamResponse:
    dist = request.app[APP_SETTINGS].frontend_dist
    index = dist / "index.html"
    if not index.is_file():
        return web.json_response(
            {
                "message": "Frontend is not built yet.",
                "hint": "Run `npm install && npm run build` in examples/knowledge_extraction_workbench/frontend.",
            },
            status=503,
        )
    return web.FileResponse(index)


async def serve_static(request: web.Request) -> web.StreamResponse:
    dist = request.app[APP_SETTINGS].frontend_dist.resolve()
    relative = request.match_info.get("path", "")
    candidate = (dist / relative).resolve()
    if candidate != dist and dist not in candidate.parents:
        raise WorkbenchError("STATIC_PATH_INVALID", "静态资源路径无效。", status=404)
    if candidate.is_file():
        return web.FileResponse(candidate)
    return await serve_index(request)


def create_app(settings: Settings | None = None) -> web.Application:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    store = Store(settings.database_path)
    store.initialize()
    secret_box = SecretBox(settings.key_path)
    service = WorkbenchService(settings, store, secret_box)
    app = web.Application(middlewares=[error_middleware], client_max_size=settings.max_upload_bytes + 1024 * 1024)
    app[APP_SETTINGS] = settings
    app[APP_STORE] = store
    app[APP_SERVICE] = service
    app[APP_SECRET_BOX] = secret_box

    app.router.add_get("/api/v1/health", health)
    app.router.add_get("/api/v1/dashboard", dashboard)
    app.router.add_get("/api/v1/scenes", list_scenes)
    app.router.add_post("/api/v1/scenes", create_scene)
    app.router.add_get("/api/v1/scenes/{scene_id}", get_scene)
    app.router.add_patch("/api/v1/scenes/{scene_id}", update_scene)
    app.router.add_delete("/api/v1/scenes/{scene_id}", archive_scene)
    app.router.add_get("/api/v1/scenes/{scene_id}/rounds", list_rounds)
    app.router.add_post("/api/v1/scenes/{scene_id}/rounds", create_round)
    app.router.add_get("/api/v1/rounds/{round_id}/materials", list_materials)
    app.router.add_post("/api/v1/rounds/{round_id}/materials", upload_round_material)
    app.router.add_patch("/api/v1/materials/{material_id}", update_material)
    app.router.add_get("/api/v1/explorations", list_explorations)
    app.router.add_post("/api/v1/explorations", create_exploration)
    app.router.add_delete("/api/v1/explorations/{exploration_id}", archive_exploration)
    app.router.add_get("/api/v1/explorations/{exploration_id}/materials", list_exploration_materials)
    app.router.add_post("/api/v1/explorations/{exploration_id}/materials", upload_exploration_material)
    app.router.add_post("/api/v1/explorations/{exploration_id}/analyze", analyze_exploration)
    app.router.add_get("/api/v1/explorations/{exploration_id}/candidates", list_candidates)
    app.router.add_post(
        "/api/v1/explorations/{exploration_id}/candidates/{candidate_id}/create-scene", candidate_create_scene
    )
    app.router.add_post("/api/v1/rounds/{round_id}/extract", start_extraction)
    app.router.add_get("/api/v1/jobs/{job_id}", get_job)
    app.router.add_get("/api/v1/jobs/{job_id}/events", job_events)
    app.router.add_post("/api/v1/jobs/{job_id}/retry", retry_job)
    app.router.add_get("/api/v1/rounds/{round_id}/document", get_document)
    app.router.add_put("/api/v1/rounds/{round_id}/document", save_document)
    app.router.add_get("/api/v1/rounds/{round_id}/revisions", list_revisions)
    app.router.add_get("/api/v1/rounds/{round_id}/suggestions", list_suggestions)
    app.router.add_post("/api/v1/rounds/{round_id}/suggestions", create_suggestion)
    app.router.add_post("/api/v1/suggestions/{suggestion_id}/apply", apply_suggestion)
    app.router.add_post("/api/v1/suggestions/{suggestion_id}/reject", reject_suggestion)
    app.router.add_get("/api/v1/rounds/{round_id}/assets", list_assets)
    app.router.add_post("/api/v1/rounds/{round_id}/assets", generate_round_assets)
    app.router.add_get("/api/v1/assets/{asset_id}/download", download_asset)
    app.router.add_post("/api/v1/rounds/{round_id}/publish", publish_round)
    app.router.add_get("/api/v1/models", list_models)
    app.router.add_post("/api/v1/models", create_model)
    app.router.add_put("/api/v1/models/{model_id}", update_model)
    app.router.add_delete("/api/v1/models/{model_id}", delete_model)
    app.router.add_post("/api/v1/models/{model_id}/test", test_model)
    app.router.add_get("/api/v1/skills", list_skills)
    app.router.add_post("/api/v1/skills", upload_skill)
    app.router.add_get("/api/v1/ability-mounts", list_ability_mounts)
    app.router.add_post("/api/v1/ability-mounts/defaults", reset_ability_mounts)
    app.router.add_put("/api/v1/ability-mounts/{mount_id}", update_ability_mount)
    app.router.add_route("*", "/api/{path:.*}", api_not_found)
    app.router.add_get("/", serve_index)
    app.router.add_get("/{path:.*}", serve_static)

    async def _cleanup(_: web.Application) -> None:
        await service.close()

    app.on_cleanup.append(_cleanup)
    return app

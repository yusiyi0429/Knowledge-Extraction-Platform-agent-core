"""Application service and bounded background jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlmodel import select

from .config import SecretBox, Settings
from .errors import WorkbenchError
from .models import (
    AbilityProfile,
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
    utc_now,
)
from .model_runtime import OpenJiuwenKnowledgeModel
from .pipeline import (
    ChunkRef,
    chunk_material,
    chunk_refs_to_json,
    chunk_text_sha256,
    fair_select_chunks,
    generate_assets,
    normalize_chunk_text,
    render_markdown,
)
from .store import Store

TERMINAL_JOB_STATUSES = {"COMPLETED", "FAILED"}
REQUIRED_ASSET_KINDS = {"RULES_XLSX", "THOUGHT_CHAIN_MD", "SKILL_ZIP", "QA_JSONL", "EVAL_JSONL"}
SUGGESTION_MODES = {"CONSISTENCY", "REGULATORY", "GAP", "CUSTOM"}


class WorkbenchService:
    """Coordinates persistent state and deterministic local generation."""

    def __init__(self, settings: Settings, store: Store, secret_box: SecretBox, test_model: Any | None = None):
        self.settings = settings
        self.store = store
        self.secret_box = secret_box
        self._test_model = test_model
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def close(self) -> None:
        active = [task for task in self._tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    def _freeze_ability_mounts(self, scope_key: str = "GLOBAL") -> list[dict[str, Any]]:
        with self.store.session() as session:
            mounts = session.exec(select(AbilityMount)).all()
            profiles = {
                item.mount_id: item
                for item in session.exec(select(AbilityProfile).where(AbilityProfile.scope_key == scope_key)).all()
            }
            frozen_mounts = []
            for mount in mounts:
                profile = profiles.get(mount.id)
                enabled = profile.enabled if profile else mount.enabled
                if not enabled:
                    continue
                model_id = profile.model_connection_id if profile else mount.model_connection_id
                skill_id = profile.skill_version_id if profile else mount.skill_version_id
                params_json = profile.params_json if profile else mount.params_json
                model = session.get(ModelConnection, model_id) if model_id else None
                skill = session.get(SkillVersion, skill_id) if skill_id else None
                frozen_mounts.append(
                    {
                        "ability_key": mount.ability_key,
                        "scope_key": scope_key,
                        "model": {
                            "id": model.id,
                            "provider": model.provider,
                            "api_base": model.api_base,
                            "model_name": model.model_name,
                            "enabled": model.enabled,
                            "credential_ciphertext": model.encrypted_api_key,
                            "updated_at": model.updated_at.isoformat(),
                        }
                        if model
                        else None,
                        "skill": {"id": skill.id, "name": skill.name, "version": skill.version} if skill else None,
                        "params": json.loads(params_json or "{}"),
                    }
                )
        return frozen_mounts

    def freeze_config(
        self,
        materials: list[Material],
        selected: list[ChunkRef],
        *,
        scope_key: str = "GLOBAL",
        ability_mounts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "template_version": "knowledge-workbench/v1",
            "model_runtime": "ability-mounts",
            "ability_scope": scope_key,
            "ability_mounts": ability_mounts or self._freeze_ability_mounts(scope_key),
            "materials": [
                {"id": item.id, "name": item.name, "sha256": item.sha256, "role": item.role}
                for item in materials
            ],
            "selected_chunks": chunk_refs_to_json(selected),
            "selection_policy": {"max_total": 24, "min_chars": 80, "strategy": "fair-full-coverage"},
        }

    def _runtime_for(self, snapshot: dict[str, Any], ability_key: str) -> Any:
        mount = next(
            (item for item in snapshot.get("ability_mounts", []) if item.get("ability_key") == ability_key),
            None,
        )
        model = mount.get("model") if mount else None
        if not model:
            if self._test_model is not None:
                return self._test_model
            raise WorkbenchError(
                "MODEL_MOUNT_REQUIRED",
                f"能力 {ability_key} 尚未挂载可用模型，请先在智能体配置中选择一个已启用的模型连接。",
                status=422,
            )
        if model.get("provider") == "FakeModel":
            raise WorkbenchError("MODEL_PROVIDER_UNSUPPORTED", "产品运行不再支持 Fake Model。", status=422)
        if not model.get("enabled", True):
            raise WorkbenchError("MODEL_DISABLED", f"能力 {ability_key} 挂载的模型已停用。", status=422)
        encrypted_key = str(model.get("credential_ciphertext", ""))
        if not encrypted_key:
            raise WorkbenchError(
                "MODEL_API_KEY_REQUIRED",
                f"能力 {ability_key} 挂载的模型没有可用 API Key。",
                status=422,
            )
        params = mount.get("params", {}) if isinstance(mount.get("params"), dict) else {}
        return OpenJiuwenKnowledgeModel(
            provider=str(model["provider"]),
            api_base=str(model.get("api_base", "")),
            model_name=str(model["model_name"]),
            api_key=self.secret_box.decrypt(encrypted_key),
            temperature=float(params.get("temperature", 0.2)),
        )

    @staticmethod
    def _mount_params(snapshot: dict[str, Any], ability_key: str) -> dict[str, Any]:
        mount = next(
            (item for item in snapshot.get("ability_mounts", []) if item.get("ability_key") == ability_key),
            None,
        )
        return mount.get("params", {}) if isinstance(mount, dict) and isinstance(mount.get("params"), dict) else {}

    def create_scene(self, payload: dict[str, Any]) -> tuple[Scene, ExtractionRound]:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise WorkbenchError("SCENE_NAME_REQUIRED", "请输入场景名称。", status=422)
        scene = Scene(
            name=name[:160],
            description=str(payload.get("description", "")).strip(),
            goal=str(payload.get("goal", "")).strip(),
            owner=str(payload.get("owner", "本机用户")).strip() or "本机用户",
        )
        with self.store.session() as session:
            session.add(scene)
            session.flush()
            round_row = ExtractionRound(
                scene_id=scene.id,
                version=1,
                subscenes_json=json.dumps(payload.get("subscenes", []), ensure_ascii=False),
            )
            session.add(round_row)
            session.commit()
            session.refresh(scene)
            session.refresh(round_row)
            session.expunge(scene)
            session.expunge(round_row)
        return scene, round_row

    def create_next_round(self, scene_id: str) -> ExtractionRound:
        with self.store.session() as session:
            scene = session.get(Scene, scene_id)
            if scene is None or scene.archived_at is not None:
                raise WorkbenchError("SCENE_NOT_FOUND", "场景不存在。", status=404)
            latest = session.exec(
                select(ExtractionRound)
                .where(ExtractionRound.scene_id == scene_id)
                .order_by(ExtractionRound.version.desc())
            ).first()
            if latest is None:
                version = 1
                subscenes_json = "[]"
                materials: list[Material] = []
            else:
                if latest.status != "PUBLISHED":
                    raise WorkbenchError(
                        "ROUND_ALREADY_EDITABLE",
                        "当前轮次仍可编辑，无需创建新轮次。",
                        status=409,
                    )
                version = latest.version + 1
                subscenes_json = latest.subscenes_json
                materials = session.exec(
                    select(Material).where(Material.round_id == latest.id, Material.enabled == True)  # noqa: E712
                ).all()
            new_round = ExtractionRound(
                scene_id=scene_id,
                version=version,
                subscenes_json=subscenes_json,
            )
            session.add(new_round)
            session.flush()
            for material in materials:
                session.add(
                    Material(
                        round_id=new_round.id,
                        name=material.name,
                        role=material.role,
                        file_path=material.file_path,
                        parsed_path=material.parsed_path,
                        extension=material.extension,
                        size_bytes=material.size_bytes,
                        sha256=material.sha256,
                    )
                )
            scene.status = "DRAFT"
            scene.updated_at = utc_now()
            session.add(scene)
            session.commit()
            session.refresh(new_round)
            session.expunge(new_round)
            return new_round

    def archive_scene(self, scene_id: str) -> None:
        with self.store.session() as session:
            scene = session.get(Scene, scene_id)
            if scene is None or scene.archived_at is not None:
                raise WorkbenchError("SCENE_NOT_FOUND", "场景不存在。", status=404)
            running = session.exec(
                select(Job).where(Job.scene_id == scene_id, Job.status.in_(["QUEUED", "RUNNING"]))
            ).first()
            if running:
                raise WorkbenchError(
                    "SCENE_JOB_CONFLICT",
                    "场景有任务正在运行，完成或失败后才能归档。",
                    status=409,
                    retryable=True,
                    details={"job_id": running.id},
                )
            scene.status = "ARCHIVED"
            scene.archived_at = utc_now()
            scene.updated_at = utc_now()
            session.add(scene)
            session.commit()

    def _load_material_chunks(self, materials: list[Material]) -> list[tuple[str, str, list[str]]]:
        result = []
        for material in materials:
            parsed_path = Path(material.parsed_path)
            if not parsed_path.is_file():
                raise WorkbenchError(
                    "MATERIAL_SOURCE_MISSING",
                    f"素材“{material.name}”的解析结果不存在，请重新上传。",
                    status=422,
                )
            text = parsed_path.read_text(encoding="utf-8")
            result.append((material.id, material.name, chunk_material(text)))
        return result

    def _new_job(
        self,
        *,
        kind: str,
        frozen_config: dict[str, Any],
        scene_id: str | None = None,
        round_id: str | None = None,
        exploration_id: str | None = None,
    ) -> Job:
        job = Job(
            kind=kind,
            scene_id=scene_id,
            round_id=round_id,
            exploration_id=exploration_id,
            frozen_config_json=json.dumps(frozen_config, ensure_ascii=False),
        )
        with self.store.session() as session:
            session.add(job)
            session.commit()
            session.refresh(job)
            session.expunge(job)
        self.store.record_job_event(
            job.id,
            phase="queued",
            status="QUEUED",
            progress=0,
            message="任务已进入队列",
        )
        return self.store.get(Job, job.id, code="JOB_NOT_FOUND")

    def _spawn(self, job: Job) -> None:
        task = asyncio.create_task(self._run_job(job.id), name=f"workbench-{job.kind.lower()}-{job.id}")
        self._tasks[job.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.id, None))

    def start_exploration(self, exploration_id: str) -> Job:
        with self.store.session() as session:
            exploration = session.get(Exploration, exploration_id)
            if exploration is None or exploration.archived_at is not None:
                raise WorkbenchError("EXPLORATION_NOT_FOUND", "探索记录不存在。", status=404)
            running = session.exec(
                select(Job).where(
                    Job.exploration_id == exploration_id,
                    Job.status.in_(["QUEUED", "RUNNING"]),
                )
            ).first()
            if running:
                raise WorkbenchError(
                    "EXPLORATION_JOB_CONFLICT",
                    "该探索正在分析中。",
                    status=409,
                    details={"job_id": running.id},
                )
            materials = session.exec(
                select(Material).where(Material.exploration_id == exploration_id, Material.enabled == True)  # noqa: E712
            ).all()
            for item in materials:
                session.expunge(item)
        if not materials:
            raise WorkbenchError("MATERIAL_REQUIRED", "请先上传至少一份素材。", status=422)
        ability_mounts = self._freeze_ability_mounts()
        mount_snapshot = {"ability_mounts": ability_mounts}
        max_chunks = max(4, min(60, int(self._mount_params(mount_snapshot, "KNOWLEDGE_EXTRACTOR").get("max_chunks", 24))))
        selected = fair_select_chunks(self._load_material_chunks(materials), max_total=max_chunks)
        job = self._new_job(
            kind="EXPLORATION",
            exploration_id=exploration_id,
            frozen_config=self.freeze_config(materials, selected, ability_mounts=ability_mounts),
        )
        self._spawn(job)
        return job

    def start_extraction(self, round_id: str) -> Job:
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, round_id)
            if round_row is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            if round_row.status == "PUBLISHED":
                raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可修改，请创建新一轮。", status=409)
            running = session.exec(
                select(Job).where(Job.round_id == round_id, Job.status.in_(["QUEUED", "RUNNING"]))
            ).first()
            if running:
                raise WorkbenchError(
                    "ROUND_JOB_CONFLICT",
                    "当前轮次已有任务运行中。",
                    status=409,
                    details={"job_id": running.id},
                )
            materials = session.exec(
                select(Material).where(Material.round_id == round_id, Material.enabled == True)  # noqa: E712
            ).all()
            if not materials:
                raise WorkbenchError("MATERIAL_REQUIRED", "请先上传至少一份启用素材。", status=422)
            scene = session.get(Scene, round_row.scene_id)
            for item in materials:
                session.expunge(item)
            scene_id = round_row.scene_id
        scope_key = f"SCENE:{scene_id}"
        ability_mounts = self._freeze_ability_mounts(scope_key)
        mount_snapshot = {"ability_mounts": ability_mounts}
        max_chunks = max(4, min(60, int(self._mount_params(mount_snapshot, "KNOWLEDGE_EXTRACTOR").get("max_chunks", 24))))
        selected = fair_select_chunks(self._load_material_chunks(materials), max_total=max_chunks)
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, round_id)
            if round_row is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            round_row.status = "EXTRACTING"
            round_row.updated_at = utc_now()
            session.add(round_row)
            scene = session.get(Scene, round_row.scene_id)
            if scene:
                scene.status = "EXTRACTING"
                scene.updated_at = utc_now()
                session.add(scene)
            session.commit()
        job = self._new_job(
            kind="EXTRACTION",
            scene_id=scene_id,
            round_id=round_id,
            frozen_config=self.freeze_config(
                materials,
                selected,
                scope_key=scope_key,
                ability_mounts=ability_mounts,
            ),
        )
        self._spawn(job)
        return job

    def start_asset_generation(self, round_id: str, *, only_kind: str | None = None) -> Job:
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, round_id)
            if round_row is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            if round_row.status == "PUBLISHED":
                raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可重新生成资产。", status=409)
            document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
            if document is None:
                raise WorkbenchError("DOCUMENT_REQUIRED", "请先完成知识萃取。", status=409)
            running = session.exec(
                select(Job).where(Job.round_id == round_id, Job.status.in_(["QUEUED", "RUNNING"]))
            ).first()
            if running:
                raise WorkbenchError("ROUND_JOB_CONFLICT", "当前轮次已有任务运行中。", status=409)
            snapshot = {
                "template_version": "knowledge-assets/v1",
                "model_runtime": "ability-mounts",
                "ability_scope": f"SCENE:{round_row.scene_id}",
                "ability_mounts": self._freeze_ability_mounts(f"SCENE:{round_row.scene_id}"),
                "document_revision": document.revision,
                "document_sha256": hashlib.sha256(document.markdown.encode()).hexdigest(),
                "only_kind": only_kind,
            }
            scene_id = round_row.scene_id
        job = self._new_job(
            kind="ASSET_GENERATION",
            scene_id=scene_id,
            round_id=round_id,
            frozen_config=snapshot,
        )
        self._spawn(job)
        return job

    async def _run_job(self, job_id: str) -> None:
        job = self.store.get(Job, job_id, code="JOB_NOT_FOUND")
        try:
            if job.kind == "EXPLORATION":
                await self._run_exploration(job)
            elif job.kind == "EXTRACTION":
                await self._run_extraction(job)
            elif job.kind == "ASSET_GENERATION":
                await self._run_asset_generation(job)
            else:
                raise WorkbenchError("JOB_KIND_UNSUPPORTED", "不支持的任务类型。", status=422)
        except asyncio.CancelledError:
            raise
        except WorkbenchError as error:
            self.store.fail_job(job.id, error)
            self._reset_failed_scope(job)
        except Exception as exc:
            error = WorkbenchError(
                "JOB_EXECUTION_FAILED",
                "任务执行失败，可查看任务状态后重试。",
                status=500,
                retryable=True,
                details={"reason": type(exc).__name__},
            )
            self.store.fail_job(job.id, error)
            self._reset_failed_scope(job)

    def _reset_failed_scope(self, job: Job) -> None:
        if not job.round_id:
            return
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, job.round_id)
            if round_row and round_row.status != "PUBLISHED":
                round_row.status = "FAILED"
                round_row.updated_at = utc_now()
                session.add(round_row)
                scene = session.get(Scene, round_row.scene_id)
                if scene:
                    scene.status = "FAILED"
                    scene.updated_at = utc_now()
                    session.add(scene)
                session.commit()

    def _selected_chunks_from_snapshot(self, snapshot: dict[str, Any]) -> list[ChunkRef]:
        material_ids = [item["id"] for item in snapshot.get("materials", [])]
        with self.store.session() as session:
            materials = [session.get(Material, material_id) for material_id in material_ids]
            if any(item is None for item in materials):
                raise WorkbenchError("MATERIAL_SOURCE_MISSING", "任务冻结的素材不存在。", status=422)
            detached = []
            for material in materials:
                assert material is not None
                expected = next(item for item in snapshot["materials"] if item["id"] == material.id)
                if material.sha256 != expected["sha256"]:
                    raise WorkbenchError("MATERIAL_HASH_CHANGED", "素材内容与任务快照不一致。", status=409)
                session.expunge(material)
                detached.append(material)
        chunk_map = {material_id: chunks for material_id, _, chunks in self._load_material_chunks(detached)}
        material_names = {material.id: material.name for material in detached}
        selected: list[ChunkRef] = []
        for reference in snapshot.get("selected_chunks", []):
            chunks = chunk_map.get(reference["material_id"], [])
            index = reference["chunk_index"]
            if index >= len(chunks):
                raise WorkbenchError("MATERIAL_CHUNK_CHANGED", "素材分块结果与任务快照不一致。", status=409)
            text = normalize_chunk_text(chunks[index])
            if chunk_text_sha256(text) != reference["text_sha256"]:
                raise WorkbenchError("MATERIAL_CHUNK_CHANGED", "素材片段与任务快照不一致。", status=409)
            selected.append(
                ChunkRef(
                    material_id=reference["material_id"],
                    material_name=material_names[reference["material_id"]],
                    chunk_index=index,
                    text=text,
                    score=float(reference.get("score", 0)),
                )
            )
        return selected

    async def _run_exploration(self, job: Job) -> None:
        self.store.record_job_event(
            job.id, phase="selecting", status="RUNNING", progress=18, message="正在公平选取跨素材高信息片段"
        )
        snapshot = json.loads(job.frozen_config_json)
        selected = self._selected_chunks_from_snapshot(snapshot)
        runtime = self._runtime_for(snapshot, "KNOWLEDGE_EXTRACTOR")
        self.store.record_job_event(
            job.id, phase="analyzing", status="RUNNING", progress=54, message="正在识别业务目标、规则和场景边界"
        )
        candidates = await runtime.explore(selected)
        with self.store.session() as session:
            exploration = session.get(Exploration, job.exploration_id)
            if exploration is None:
                raise WorkbenchError("EXPLORATION_NOT_FOUND", "探索记录不存在。", status=404)
            existing = session.exec(
                select(ExplorationCandidate).where(ExplorationCandidate.exploration_id == exploration.id)
            ).all()
            for row in existing:
                session.delete(row)
            for candidate in candidates:
                session.add(
                    ExplorationCandidate(
                        exploration_id=exploration.id,
                        name=candidate["name"],
                        description=candidate["description"],
                        goal=candidate["goal"],
                        confidence=candidate["confidence"],
                        source_refs_json=json.dumps(candidate["source_refs"], ensure_ascii=False),
                    )
                )
            exploration.status = "READY"
            exploration.updated_at = utc_now()
            session.add(exploration)
            session.commit()
        self.store.record_job_event(
            job.id, phase="completed", status="COMPLETED", progress=100, message=f"已生成 {len(candidates)} 个候选场景"
        )

    async def _run_extraction(self, job: Job) -> None:
        snapshot = json.loads(job.frozen_config_json)
        self.store.record_job_event(
            job.id, phase="selecting", status="RUNNING", progress=10, message="正在校验素材哈希与片段快照"
        )
        selected = self._selected_chunks_from_snapshot(snapshot)
        runtime = self._runtime_for(snapshot, "KNOWLEDGE_EXTRACTOR")
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, job.round_id)
            scene = session.get(Scene, round_row.scene_id) if round_row else None
            if round_row is None or scene is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            scene_name = scene.name
        self.store.record_job_event(
            job.id, phase="mapping", status="RUNNING", progress=28, message=f"正在分块萃取 {len(selected)} 个证据片段"
        )
        concurrency = max(1, min(6, int(self._mount_params(snapshot, "KNOWLEDGE_EXTRACTOR").get("concurrency", 3))))
        semaphore = asyncio.Semaphore(concurrency)

        async def _map(sequence: int, chunk: ChunkRef) -> dict[str, Any]:
            async with semaphore:
                return await runtime.map_chunk(chunk, sequence)

        mapped = await asyncio.gather(*(_map(index, chunk) for index, chunk in enumerate(selected, start=1)))
        self.store.record_job_event(
            job.id, phase="reducing", status="RUNNING", progress=68, message="正在合并规则、去重并还原流程"
        )
        structured = await runtime.reduce(mapped, scene_name)
        markdown = render_markdown(structured)
        self.store.record_job_event(
            job.id, phase="persisting", status="RUNNING", progress=88, message="正在保存研判文档与初始修订"
        )
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, job.round_id)
            if round_row is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_row.id)).first()
            if document is None:
                document = KnowledgeDocument(
                    round_id=round_row.id,
                    markdown=markdown,
                    structured_json=json.dumps(structured, ensure_ascii=False),
                    revision=1,
                )
                session.add(document)
                session.flush()
            else:
                document.revision += 1
                document.markdown = markdown
                document.structured_json = json.dumps(structured, ensure_ascii=False)
                document.updated_at = utc_now()
                session.add(document)
            session.add(
                Revision(
                    document_id=document.id,
                    revision=document.revision,
                    markdown=markdown,
                    reason=f"{runtime.model_id} 受控 Map/Reduce 萃取",
                    author="系统",
                )
            )
            round_row.status = "READY"
            round_row.frozen_config_json = job.frozen_config_json
            round_row.updated_at = utc_now()
            session.add(round_row)
            scene = session.get(Scene, round_row.scene_id)
            if scene:
                scene.status = "REVIEW"
                scene.updated_at = utc_now()
                session.add(scene)
            session.commit()
        self.store.record_job_event(
            job.id, phase="completed", status="COMPLETED", progress=100, message="知识萃取完成，可进入对齐与修订"
        )

    async def _run_asset_generation(self, job: Job) -> None:
        self.store.record_job_event(
            job.id, phase="preparing", status="RUNNING", progress=12, message="正在校验文档修订与资产模板"
        )
        snapshot = json.loads(job.frozen_config_json)
        qa_runtime = self._runtime_for(snapshot, "QA_GENERATOR")
        evaluation_runtime = self._runtime_for(snapshot, "EVALUATION_GENERATOR")
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, job.round_id)
            document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == job.round_id)).first()
            scene = session.get(Scene, round_row.scene_id) if round_row else None
            if round_row is None or document is None or scene is None:
                raise WorkbenchError("DOCUMENT_REQUIRED", "知识文档不存在。", status=409)
            if document.revision != snapshot["document_revision"]:
                raise WorkbenchError("DOCUMENT_REVISION_CONFLICT", "知识文档已发生变化，请重新生成资产。", status=409)
            markdown = document.markdown
            structured = json.loads(document.structured_json)
            scene_name = scene.name
        self.store.record_job_event(
            job.id, phase="generating", status="RUNNING", progress=48, message="正在生成规则、Skill、QA 与评测资产"
        )
        output_dir = self.settings.asset_dir / str(job.round_id) / f"job-{job.id}"
        specs = await generate_assets(
            output_dir,
            scene_name,
            markdown,
            structured,
            qa_runtime,
            evaluation_runtime,
        )
        only_kind = snapshot.get("only_kind")
        if only_kind:
            specs = [spec for spec in specs if spec.kind == only_kind]
            if not specs:
                raise WorkbenchError("ASSET_KIND_INVALID", "不支持的资产类型。", status=422)
        with self.store.session() as session:
            for spec in specs:
                previous = session.exec(
                    select(Asset).where(Asset.round_id == job.round_id, Asset.kind == spec.kind).order_by(Asset.version.desc())
                ).first()
                session.add(
                    Asset(
                        round_id=str(job.round_id),
                        kind=spec.kind,
                        filename=spec.filename,
                        file_path=str(spec.path),
                        mime_type=spec.mime_type,
                        version=(previous.version + 1) if previous else 1,
                        source_revision=snapshot["document_revision"],
                        synthetic=spec.synthetic,
                    )
                )
            session.commit()
        self.store.record_job_event(
            job.id, phase="completed", status="COMPLETED", progress=100, message=f"已生成 {len(specs)} 类知识资产"
        )

    def retry_job(self, job_id: str) -> Job:
        original = self.store.get(Job, job_id, code="JOB_NOT_FOUND")
        if original.status != "FAILED" or not original.retryable:
            raise WorkbenchError("JOB_NOT_RETRYABLE", "该任务当前不可重试。", status=409)
        job = self._new_job(
            kind=original.kind,
            scene_id=original.scene_id,
            round_id=original.round_id,
            exploration_id=original.exploration_id,
            frozen_config=json.loads(original.frozen_config_json),
        )
        self._spawn(job)
        return job

    def publish_round(self, round_id: str) -> ExtractionRound:
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, round_id)
            if round_row is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            if round_row.status == "PUBLISHED":
                session.expunge(round_row)
                return round_row
            assets = session.exec(select(Asset).where(Asset.round_id == round_id)).all()
            latest_kinds = {asset.kind for asset in assets}
            missing = sorted(REQUIRED_ASSET_KINDS - latest_kinds)
            if missing:
                raise WorkbenchError(
                    "ASSETS_INCOMPLETE",
                    "五类资产尚未全部生成。",
                    status=409,
                    details={"missing": missing},
                )
            document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
            if document is None:
                raise WorkbenchError("DOCUMENT_REQUIRED", "知识文档不存在。", status=409)
            stale = sorted(
                kind
                for kind in REQUIRED_ASSET_KINDS
                if not any(asset.kind == kind and asset.source_revision == document.revision for asset in assets)
            )
            if stale:
                raise WorkbenchError(
                    "ASSETS_STALE",
                    "部分资产不是基于当前文档修订生成，请重新生成后发布。",
                    status=409,
                    details={"stale": stale, "document_revision": document.revision},
                )
            round_row.status = "PUBLISHED"
            round_row.published_at = utc_now()
            round_row.updated_at = utc_now()
            scene = session.get(Scene, round_row.scene_id)
            if scene:
                scene.status = "PUBLISHED"
                scene.updated_at = utc_now()
                session.add(scene)
            session.add(round_row)
            session.commit()
            session.refresh(round_row)
            session.expunge(round_row)
            return round_row

    async def create_suggestion(
        self,
        round_id: str,
        *,
        mode: str = "CONSISTENCY",
        instruction: str = "",
    ) -> Suggestion:
        normalized_mode = mode.strip().upper()
        normalized_instruction = instruction.strip()
        if normalized_mode not in SUGGESTION_MODES:
            raise WorkbenchError("SUGGESTION_MODE_INVALID", "不支持该 AI 检查模式。", status=422)
        if normalized_mode == "CUSTOM" and not normalized_instruction:
            raise WorkbenchError("SUGGESTION_INSTRUCTION_REQUIRED", "请输入希望 AI 如何修改文档。", status=422)
        if len(normalized_instruction) > 1000:
            raise WorkbenchError("SUGGESTION_INSTRUCTION_TOO_LONG", "修改指令不能超过 1000 个字符。", status=422)
        with self.store.session() as session:
            round_row = session.get(ExtractionRound, round_id)
            if round_row is None:
                raise WorkbenchError("ROUND_NOT_FOUND", "萃取轮次不存在。", status=404)
            if round_row.status == "PUBLISHED":
                raise WorkbenchError("ROUND_IMMUTABLE", "已发布轮次不可修改。", status=409)
            document = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.round_id == round_id)).first()
            if document is None:
                raise WorkbenchError("DOCUMENT_REQUIRED", "请先完成知识萃取。", status=409)
            markdown = document.markdown
            structured = json.loads(document.structured_json)
            revision = document.revision
        snapshot = {
            "ability_scope": f"SCENE:{round_row.scene_id}",
            "ability_mounts": self._freeze_ability_mounts(f"SCENE:{round_row.scene_id}"),
        }
        runtime = self._runtime_for(snapshot, "ALIGNMENT_REVIEWER")
        suggestion_data = await runtime.suggest(
            markdown,
            structured,
            revision,
            mode=normalized_mode,
            instruction=normalized_instruction,
        )
        suggestion = Suggestion(
            round_id=round_id,
            base_revision=revision,
            old_text=suggestion_data["old_text"],
            new_text=suggestion_data["new_text"],
            explanation=suggestion_data["explanation"],
            source_refs_json=json.dumps(suggestion_data["source_refs"], ensure_ascii=False),
        )
        with self.store.session() as session:
            session.add(suggestion)
            session.commit()
            session.refresh(suggestion)
            session.expunge(suggestion)
        return suggestion

    def apply_suggestion(self, suggestion_id: str, *, expected_revision: int) -> KnowledgeDocument:
        with self.store.session() as session:
            suggestion = session.get(Suggestion, suggestion_id)
            if suggestion is None:
                raise WorkbenchError("SUGGESTION_NOT_FOUND", "建议不存在。", status=404)
            if suggestion.status != "PENDING":
                raise WorkbenchError("SUGGESTION_RESOLVED", "该建议已处理。", status=409)
            round_row = session.get(ExtractionRound, suggestion.round_id)
            if round_row is None or round_row.status == "PUBLISHED":
                raise WorkbenchError("ROUND_IMMUTABLE", "当前轮次不可修改。", status=409)
            document = session.exec(
                select(KnowledgeDocument).where(KnowledgeDocument.round_id == suggestion.round_id)
            ).first()
            if document is None:
                raise WorkbenchError("DOCUMENT_REQUIRED", "知识文档不存在。", status=409)
            if document.revision != expected_revision or suggestion.base_revision != document.revision:
                raise WorkbenchError(
                    "DOCUMENT_REVISION_CONFLICT",
                    "文档已发生变化，请刷新后重新生成建议。",
                    status=409,
                    details={"current_revision": document.revision},
                )
            if suggestion.old_text not in document.markdown:
                raise WorkbenchError("SUGGESTION_CONTEXT_CHANGED", "建议对应的原文已变化。", status=409)
            document.markdown = document.markdown.replace(suggestion.old_text, suggestion.new_text, 1)
            document.revision += 1
            document.updated_at = utc_now()
            suggestion.status = "APPLIED"
            suggestion.resolved_at = utc_now()
            session.add(document)
            session.add(suggestion)
            session.add(
                Revision(
                    document_id=document.id,
                    revision=document.revision,
                    markdown=document.markdown,
                    reason=f"采纳 AI 建议：{suggestion.explanation[:120]}",
                    author="本机用户",
                )
            )
            session.commit()
            session.refresh(document)
            session.expunge(document)
            return document

    def reject_suggestion(self, suggestion_id: str) -> Suggestion:
        with self.store.session() as session:
            suggestion = session.get(Suggestion, suggestion_id)
            if suggestion is None:
                raise WorkbenchError("SUGGESTION_NOT_FOUND", "建议不存在。", status=404)
            if suggestion.status != "PENDING":
                raise WorkbenchError("SUGGESTION_RESOLVED", "该建议已处理。", status=409)
            suggestion.status = "REJECTED"
            suggestion.resolved_at = utc_now()
            session.add(suggestion)
            session.commit()
            session.refresh(suggestion)
            session.expunge(suggestion)
            return suggestion

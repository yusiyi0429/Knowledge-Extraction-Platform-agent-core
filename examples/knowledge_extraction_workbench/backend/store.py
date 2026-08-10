"""Small SQLite persistence layer for the workbench."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, func, select

from .errors import WorkbenchError
from .models import (
    AbilityProfile,
    AbilityMount,
    Asset,
    ExtractionRound,
    Job,
    JobEvent,
    ModelConnection,
    Scene,
    SkillVersion,
    utc_now,
)

TableT = TypeVar("TableT", bound=SQLModel)

ABILITY_SPECS = (
    {
        "key": "KNOWLEDGE_EXTRACTOR",
        "legacy_key": "RULE_EXTRACTOR",
        "name": "知识萃取智能体",
        "description": "从制度与案例中一次萃取规则、研判流程与来源引用，形成结构化研判文档。",
        "stage": "EXTRACTION",
        "trigger": "点击「开始萃取」",
        "location": "环节二 · 研判文档",
        "skill_slug": "rule-extraction",
        "defaults": {
            "temperature": 0.1,
            "max_chunks": 24,
            "concurrency": 3,
            "stability": "STRICT",
        },
    },
    {
        "key": "ALIGNMENT_REVIEWER",
        "legacy_key": "ALIGNMENT_REVIEWER",
        "name": "冲突检测与对齐智能体",
        "description": "执行一致性检查、查漏补缺和监管对齐，只生成可审查建议，不直接修改文档。",
        "stage": "EXTRACTION",
        "trigger": "对齐时 / AI 助手",
        "location": "环节二 · 对齐 AI 助手",
        "skill_slug": "conflict-alignment",
        "defaults": {"temperature": 0.1, "stability": "STRICT"},
    },
    {
        "key": "RULE_GENERATOR",
        "legacy_key": "ASSET_GENERATOR",
        "name": "规则库生成智能体",
        "description": "从定稿研判文档抽取 IF-THEN 条目，生成独立规则清单与规范化 JSON。",
        "stage": "GENERATION",
        "trigger": "点击「生成」",
        "location": "环节三 · 规则清单资产",
        "skill_slug": "rule-extraction",
        "defaults": {"temperature": 0.0, "output_format": "XLSX_JSON"},
    },
    {
        "key": "THOUGHT_CHAIN_GENERATOR",
        "legacy_key": "PROCESS_EXTRACTOR",
        "name": "思维链生成智能体",
        "description": "把章节流程、分支与循环整理为可审计的决策研判链，不输出模型隐藏思维。",
        "stage": "GENERATION",
        "trigger": "点击「生成」",
        "location": "环节三 · 思维链资产",
        "skill_slug": "thought-chain",
        "defaults": {"temperature": 0.0, "output_format": "MARKDOWN_OUTLINE"},
    },
    {
        "key": "SKILL_GENERATOR",
        "legacy_key": "MATERIAL_ANALYST",
        "name": "Skill 生成智能体",
        "description": "把定稿规则和研判链打包为 openJiuwen SKILL.md 规范资产，并保留精选范例。",
        "stage": "GENERATION",
        "trigger": "进入环节三 / 点击「生成」",
        "location": "环节三 · Skill 资产",
        "skill_slug": "skill-packaging",
        "defaults": {"temperature": 0.0, "few_shot_count": 8, "package_format": "OPENJIUWEN"},
    },
    {
        "key": "QA_GENERATOR",
        "legacy_key": "SCENE_EXPLORER",
        "name": "QA 对生成智能体",
        "description": "从规则与案例衍生带来源问答语料，供检索增强与后续人工复核。",
        "stage": "GENERATION",
        "trigger": "点击「生成」",
        "location": "环节三 · QA 对资产",
        "skill_slug": "qa-generation",
        "defaults": {"temperature": 0.2, "question_style": "BUSINESS", "density": "STANDARD"},
    },
    {
        "key": "EVALUATION_GENERATOR",
        "legacy_key": "EVALUATOR",
        "name": "评测集生成智能体",
        "description": "从留出案例或已审定规则构造测试集，明确标记合成数据并防止数据泄漏。",
        "stage": "GENERATION",
        "trigger": "点击「生成」",
        "location": "环节三 · 评测集资产",
        "skill_slug": "evaluation-construction",
        "defaults": {
            "temperature": 0.1,
            "test_split": 20,
            "boundary_coverage": "HIGH",
        },
    },
)

ABILITY_SPEC_BY_KEY = {item["key"]: item for item in ABILITY_SPECS}

DEFAULT_SKILLS = (
    {
        "slug": "rule-extraction",
        "name": "规则萃取 Skill",
        "description": "从文本提取 IF-THEN 判据、例外和来源的方法论骨架，也供规则清单生成复用。",
        "version": "2.0.0",
    },
    {
        "slug": "thought-chain",
        "name": "思维链萃取 Skill",
        "description": "抽取研判节点、分支与循环并整理为可审计决策链的方法论。",
        "version": "2.0.0",
    },
    {
        "slug": "conflict-alignment",
        "name": "冲突检测 Skill",
        "description": "检测规则冲突、执行一致性检查并形成可采纳差异建议。",
        "version": "1.2.0",
    },
    {
        "slug": "skill-packaging",
        "name": "Skill 打包 Skill",
        "description": "按 openJiuwen 的 SKILL.md、scripts、references、assets 结构打包交付物。",
        "version": "1.3.0",
    },
    {
        "slug": "qa-generation",
        "name": "QA 生成 Skill",
        "description": "从规则与案例衍生带来源问答对，并控制问法风格与生成密度。",
        "version": "1.4.0",
    },
    {
        "slug": "evaluation-construction",
        "name": "评测集构造 Skill",
        "description": "从留出案例或审定规则构造测试集，标记合成数据并避免训练评测泄漏。",
        "version": "1.1.0",
    },
    {
        "slug": "feedback-analysis",
        "name": "错例分析与回流 Skill",
        "description": "为判别式与生成式任务提供错因分析、专家修订和知识缺口回流的通用方法。",
        "version": "1.0.0",
    },
)


def _skill_manifest(skill: SkillVersion) -> dict[str, Any]:
    try:
        value = json.loads(skill.manifest_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class Store:
    """Thread-safe session factory and common persistence operations."""

    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
        )
        self._lock = RLock()

        @event.listens_for(self.engine, "connect")
        def _configure_sqlite(connection: Any, _: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._lock, Session(self.engine) as session:
            yield session

    def initialize(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        self._seed_defaults()
        self._mark_interrupted_jobs()

    def _seed_defaults(self) -> None:
        with self.session() as session:
            models = session.exec(select(ModelConnection).order_by(ModelConnection.created_at)).all()
            fake_models = [model for model in models if model.provider == "FakeModel"]
            real_models = [model for model in models if model.provider != "FakeModel" and model.enabled]
            available_models = [model for model in real_models if model.encrypted_api_key]
            default_model = available_models[0] if len(available_models) == 1 else None

            all_skills = session.exec(select(SkillVersion)).all()
            skills_by_slug = {
                str(_skill_manifest(item).get("slug")): item
                for item in all_skills
                if _skill_manifest(item).get("slug")
            }
            seeded_skills: dict[str, SkillVersion] = {}
            for definition in DEFAULT_SKILLS:
                skill = skills_by_slug.get(definition["slug"])
                if skill is None:
                    skill = SkillVersion(
                        name=definition["name"],
                        description=definition["description"],
                        version=definition["version"],
                        manifest_json=json.dumps(
                            {
                                "built_in": True,
                                "kind": "TEMPLATE",
                                "read_only": True,
                                "slug": definition["slug"],
                                "lineage_id": f"template:{definition['slug']}",
                                "scene_name": "通用场景",
                            },
                            ensure_ascii=False,
                        ),
                    )
                    session.add(skill)
                else:
                    skill.name = definition["name"]
                    skill.description = definition["description"]
                    skill.version = definition["version"]
                    skill.status = "ENABLED"
                    manifest = _skill_manifest(skill)
                    skill.manifest_json = json.dumps(
                        manifest
                        | {
                            "built_in": True,
                            "kind": "TEMPLATE",
                            "read_only": True,
                            "slug": definition["slug"],
                            "lineage_id": f"template:{definition['slug']}",
                            "scene_name": "通用场景",
                        },
                        ensure_ascii=False,
                    )
                    session.add(skill)
                seeded_skills[definition["slug"]] = skill

            active_template_slugs = set(seeded_skills)
            for skill in all_skills:
                manifest = _skill_manifest(skill)
                if manifest.get("built_in") and manifest.get("slug") not in active_template_slugs:
                    skill.status = "ARCHIVED"
                    session.add(skill)
            session.flush()

            existing_mounts = session.exec(select(AbilityMount)).all()
            mounts_by_key = {item.ability_key: item for item in existing_mounts}
            models_by_id = {model.id: model for model in models}
            used_mount_ids: set[str] = set()
            for spec in ABILITY_SPECS:
                mount = mounts_by_key.get(spec["key"])
                migrated = False
                if mount is None:
                    mount = mounts_by_key.get(spec["legacy_key"])
                    migrated = mount is not None
                if mount is None:
                    mount = AbilityMount(
                        ability_key=spec["key"],
                        display_name=spec["name"],
                        description=spec["description"],
                    )
                    session.add(mount)
                    migrated = True
                mount.ability_key = spec["key"]
                mount.display_name = spec["name"]
                mount.description = spec["description"]
                current_model = models_by_id.get(str(mount.model_connection_id))
                if current_model is None or current_model.provider == "FakeModel" or not current_model.enabled:
                    mount.model_connection_id = default_model.id if default_model else None
                    mount.enabled = bool(mount.model_connection_id)
                current_skill = session.get(SkillVersion, mount.skill_version_id) if mount.skill_version_id else None
                if migrated or current_skill is None or current_skill.status != "ENABLED":
                    mount.skill_version_id = seeded_skills[spec["skill_slug"]].id
                try:
                    current_params = json.loads(mount.params_json or "{}")
                except (TypeError, json.JSONDecodeError):
                    current_params = {}
                if not isinstance(current_params, dict):
                    current_params = {}
                mount.params_json = json.dumps(
                    spec["defaults"] if migrated else spec["defaults"] | current_params,
                    ensure_ascii=False,
                )
                mount.updated_at = utc_now()
                session.add(mount)
                session.flush()
                used_mount_ids.add(mount.id)

            for mount in existing_mounts:
                if mount.id in used_mount_ids:
                    continue
                profiles = session.exec(select(AbilityProfile).where(AbilityProfile.mount_id == mount.id)).all()
                for profile in profiles:
                    session.delete(profile)
                session.delete(mount)

            profiles = session.exec(select(AbilityProfile)).all()
            fake_ids = {model.id for model in fake_models}
            for profile in profiles:
                if profile.model_connection_id in fake_ids:
                    profile.model_connection_id = default_model.id if default_model else None
                    profile.enabled = bool(profile.model_connection_id)
                    session.add(profile)

            session.flush()
            for fake in fake_models:
                session.delete(fake)
            session.commit()

    def _mark_interrupted_jobs(self) -> None:
        with self.session() as session:
            interrupted = session.exec(select(Job).where(Job.status.in_(["QUEUED", "RUNNING"]))).all()
            for job in interrupted:
                job.status = "FAILED"
                job.phase = "interrupted"
                job.message = "服务重启中断了任务，可显式重试"
                job.error_code = "SERVICE_RESTARTED"
                job.error_message = "服务重启中断了未完成任务"
                job.retryable = True
                job.updated_at = utc_now()
                job.seq += 1
                session.add(job)
                session.add(
                    JobEvent(
                        job_id=job.id,
                        seq=job.seq,
                        phase=job.phase,
                        status=job.status,
                        progress=job.progress,
                        message=job.message,
                    )
                )
            session.commit()

    def get(self, model: type[TableT], object_id: str, *, code: str = "NOT_FOUND") -> TableT:
        with self.session() as session:
            instance = session.get(model, object_id)
            if instance is None:
                raise WorkbenchError(code, "请求的资源不存在。", status=404)
            session.expunge(instance)
            return instance

    def record_job_event(
        self,
        job_id: str,
        *,
        phase: str,
        status: str,
        progress: int,
        message: str,
    ) -> JobEvent:
        with self.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise WorkbenchError("JOB_NOT_FOUND", "任务不存在。", status=404)
            job.seq += 1
            job.phase = phase
            job.status = status
            job.progress = max(0, min(100, progress))
            job.message = message[:500]
            job.updated_at = utc_now()
            event_row = JobEvent(
                job_id=job.id,
                seq=job.seq,
                phase=phase,
                status=status,
                progress=job.progress,
                message=job.message,
            )
            session.add(job)
            session.add(event_row)
            session.commit()
            session.refresh(event_row)
            session.expunge(event_row)
            return event_row

    def fail_job(self, job_id: str, error: WorkbenchError) -> None:
        with self.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.error_code = error.code
            job.error_message = error.message[:500]
            job.retryable = error.retryable
            progress = job.progress
            session.add(job)
            session.commit()
        self.record_job_event(
            job_id,
            phase="failed",
            status="FAILED",
            progress=progress,
            message=error.message,
        )

    def events_after(self, job_id: str, seq: int) -> list[JobEvent]:
        with self.session() as session:
            rows = session.exec(
                select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.seq > seq).order_by(JobEvent.seq)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def dashboard_counts(self) -> dict[str, int]:
        with self.session() as session:
            scenes = session.exec(
                select(func.count()).select_from(Scene).where(Scene.archived_at.is_(None))
            ).one()
            rules = 0
            documents = session.exec(
                select(ExtractionRound).where(ExtractionRound.status.in_(["READY", "PUBLISHED"]))
            ).all()
            for round_row in documents:
                # Rule count is computed from generated assets when present; the UI also shows zero honestly.
                rule_asset = session.exec(
                    select(Asset).where(Asset.round_id == round_row.id, Asset.kind == "RULES_XLSX")
                ).first()
                if rule_asset:
                    rules += 1
            published = session.exec(
                select(func.count()).select_from(ExtractionRound).where(ExtractionRound.status == "PUBLISHED")
            ).one()
            skills = session.exec(
                select(func.count()).select_from(SkillVersion).where(SkillVersion.status == "ENABLED")
            ).one()
        return {
            "scenes": int(scenes),
            "rules": rules,
            "published_rounds": int(published),
            "skills": int(skills),
        }

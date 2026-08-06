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
    ("SCENE_EXPLORER", "场景探索智能体", "跨素材识别高价值业务场景与边界"),
    ("MATERIAL_ANALYST", "素材分析智能体", "解析素材角色、证据位置与信息质量"),
    ("RULE_EXTRACTOR", "规则萃取智能体", "提取条件、动作、例外与来源引用"),
    ("PROCESS_EXTRACTOR", "流程萃取智能体", "还原步骤、分支、输入与产出"),
    ("ALIGNMENT_REVIEWER", "一致性对齐智能体", "识别冲突、缺口并提出可审查修订"),
    ("ASSET_GENERATOR", "知识生成智能体", "从规范化知识确定性生成交付资产"),
    ("EVALUATOR", "评测智能体", "生成带来源的问答与合成评测样本"),
)

DEFAULT_SKILLS = (
    ("scene-discovery", "业务场景探索", "跨素材发现候选场景并给出来源依据"),
    ("knowledge-map-reduce", "知识 Map/Reduce", "带引用的规则与流程分块萃取、去重和冲突检测"),
    ("alignment-review", "知识一致性对齐", "形成可采纳、可放弃的差异建议"),
    ("asset-publisher", "知识资产发布", "生成 Skill、规则、QA 与合成评测集"),
)


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
            fake = session.exec(select(ModelConnection).where(ModelConnection.provider == "FakeModel")).first()
            if fake is None:
                fake = ModelConnection(
                    name="内置 Fake Model",
                    provider="FakeModel",
                    api_base="local://deterministic",
                    model_name="fake-knowledge-extractor-v1",
                )
                session.add(fake)
                session.flush()

            skills = {item.name: item for item in session.exec(select(SkillVersion)).all()}
            for name, display_name, description in DEFAULT_SKILLS:
                if name not in skills:
                    skill = SkillVersion(
                        name=name,
                        description=f"{display_name}：{description}",
                        version="1.0.0",
                        manifest_json=json.dumps({"built_in": True}, ensure_ascii=False),
                    )
                    session.add(skill)
                    skills[name] = skill
            session.flush()

            mounts = {item.ability_key for item in session.exec(select(AbilityMount)).all()}
            skill_order = [skills[item[0]] for item in DEFAULT_SKILLS]
            for index, (ability_key, display_name, description) in enumerate(ABILITY_SPECS):
                if ability_key in mounts:
                    continue
                skill = skill_order[min(index // 2, len(skill_order) - 1)]
                session.add(
                    AbilityMount(
                        ability_key=ability_key,
                        display_name=display_name,
                        description=description,
                        model_connection_id=fake.id,
                        skill_version_id=skill.id,
                        params_json=json.dumps(
                            {"temperature": 0.2, "max_chunks": 24, "concurrency": 3}, ensure_ascii=False
                        ),
                    )
                )
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
            session.add(job)
            session.commit()
        self.record_job_event(
            job_id,
            phase="failed",
            status="FAILED",
            progress=job.progress,
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

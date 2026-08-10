"""SQLModel tables used only by the standalone workbench example."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid4())


class Scene(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True, max_length=160)
    description: str = Field(default="", sa_column=Column(Text))
    goal: str = Field(default="", sa_column=Column(Text))
    owner: str = Field(default="本机用户", max_length=80)
    status: str = Field(default="DRAFT", index=True, max_length=32)
    archived_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExtractionRound(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("scene_id", "version", name="uq_round_scene_version"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    scene_id: str = Field(foreign_key="scene.id", index=True)
    version: int = Field(default=1, ge=1)
    status: str = Field(default="DRAFT", index=True, max_length=32)
    subscenes_json: str = Field(default="[]", sa_column=Column(Text))
    frozen_config_json: str = Field(default="{}", sa_column=Column(Text))
    published_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Exploration(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(default="未命名探索", max_length=160)
    goal: str = Field(default="", sa_column=Column(Text))
    status: str = Field(default="DRAFT", index=True, max_length=32)
    archived_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Material(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    round_id: Optional[str] = Field(default=None, foreign_key="extractionround.id", index=True)
    exploration_id: Optional[str] = Field(default=None, foreign_key="exploration.id", index=True)
    name: str = Field(max_length=255)
    role: str = Field(default="REFERENCE", max_length=32)
    file_path: str = Field(sa_column=Column(Text))
    parsed_path: str = Field(default="", sa_column=Column(Text))
    extension: str = Field(max_length=16)
    size_bytes: int = Field(default=0)
    sha256: str = Field(max_length=64, index=True)
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class Job(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    kind: str = Field(index=True, max_length=40)
    status: str = Field(default="QUEUED", index=True, max_length=24)
    phase: str = Field(default="queued", max_length=64)
    progress: int = Field(default=0, ge=0, le=100)
    seq: int = Field(default=0, ge=0)
    message: str = Field(default="已进入队列", max_length=500)
    scene_id: Optional[str] = Field(default=None, foreign_key="scene.id", index=True)
    round_id: Optional[str] = Field(default=None, foreign_key="extractionround.id", index=True)
    exploration_id: Optional[str] = Field(default=None, foreign_key="exploration.id", index=True)
    frozen_config_json: str = Field(default="{}", sa_column=Column(Text))
    error_code: str = Field(default="", max_length=80)
    error_message: str = Field(default="", max_length=500)
    retryable: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class JobEvent(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_event_seq"),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="job.id", index=True)
    seq: int = Field(index=True)
    phase: str = Field(max_length=64)
    status: str = Field(max_length=24)
    progress: int = Field(ge=0, le=100)
    message: str = Field(max_length=500)
    created_at: datetime = Field(default_factory=utc_now)


class ExplorationCandidate(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    exploration_id: str = Field(foreign_key="exploration.id", index=True)
    name: str = Field(max_length=160)
    description: str = Field(default="", sa_column=Column(Text))
    goal: str = Field(default="", sa_column=Column(Text))
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_refs_json: str = Field(default="[]", sa_column=Column(Text))
    created_scene_id: Optional[str] = Field(default=None, foreign_key="scene.id")
    created_at: datetime = Field(default_factory=utc_now)


class KnowledgeDocument(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("round_id", name="uq_document_round"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    round_id: str = Field(foreign_key="extractionround.id", index=True)
    markdown: str = Field(default="", sa_column=Column(Text))
    structured_json: str = Field(default="{}", sa_column=Column(Text))
    revision: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class Revision(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    document_id: str = Field(foreign_key="knowledgedocument.id", index=True)
    revision: int = Field(ge=1)
    markdown: str = Field(sa_column=Column(Text))
    reason: str = Field(default="手工保存", max_length=200)
    author: str = Field(default="本机用户", max_length=80)
    created_at: datetime = Field(default_factory=utc_now)


class Suggestion(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    round_id: str = Field(foreign_key="extractionround.id", index=True)
    base_revision: int = Field(ge=1)
    old_text: str = Field(sa_column=Column(Text))
    new_text: str = Field(sa_column=Column(Text))
    explanation: str = Field(sa_column=Column(Text))
    source_refs_json: str = Field(default="[]", sa_column=Column(Text))
    status: str = Field(default="PENDING", index=True, max_length=24)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = Field(default=None)


class Asset(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    round_id: str = Field(foreign_key="extractionround.id", index=True)
    kind: str = Field(index=True, max_length=40)
    filename: str = Field(max_length=255)
    file_path: str = Field(sa_column=Column(Text))
    mime_type: str = Field(default="application/octet-stream", max_length=120)
    version: int = Field(default=1, ge=1)
    source_revision: int = Field(default=0, ge=0)
    synthetic: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)


class ModelConnection(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True, max_length=120)
    provider: str = Field(max_length=40)
    api_base: str = Field(default="", max_length=500)
    model_name: str = Field(default="", max_length=160)
    encrypted_api_key: str = Field(default="", sa_column=Column(Text))
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SkillVersion(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True, max_length=120)
    description: str = Field(default="", sa_column=Column(Text))
    version: str = Field(default="1.0.0", max_length=40)
    status: str = Field(default="ENABLED", index=True, max_length=24)
    package_path: str = Field(default="", sa_column=Column(Text))
    manifest_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)


class AbilityMount(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("ability_key", name="uq_ability_mount_key"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    ability_key: str = Field(index=True, max_length=64)
    display_name: str = Field(max_length=100)
    description: str = Field(default="", sa_column=Column(Text))
    enabled: bool = Field(default=True)
    model_connection_id: Optional[str] = Field(default=None, foreign_key="modelconnection.id")
    skill_version_id: Optional[str] = Field(default=None, foreign_key="skillversion.id")
    params_json: str = Field(default="{}", sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=utc_now)


class AbilityProfile(SQLModel, table=True):
    """Scene-scoped overrides for an ability mount; GLOBAL remains on AbilityMount."""

    __table_args__ = (UniqueConstraint("mount_id", "scope_key", name="uq_ability_profile_scope"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    mount_id: str = Field(foreign_key="abilitymount.id", index=True)
    scope_key: str = Field(index=True, max_length=240)
    enabled: bool = Field(default=True)
    model_connection_id: Optional[str] = Field(default=None, foreign_key="modelconnection.id")
    skill_version_id: Optional[str] = Field(default=None, foreign_key="skillversion.id")
    params_json: str = Field(default="{}", sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=utc_now)


class EvaluationRun(SQLModel, table=True):
    """A frozen evaluation experiment for one published scene Skill."""

    id: str = Field(default_factory=new_id, primary_key=True)
    round_id: str = Field(foreign_key="extractionround.id", index=True)
    model_connection_id: str = Field(foreign_key="modelconnection.id", index=True)
    job_id: Optional[str] = Field(default=None, foreign_key="job.id", index=True)
    dataset_name: str = Field(max_length=255)
    dataset_kind: str = Field(default="GENERATED", max_length=24)
    dataset_path: str = Field(sa_column=Column(Text))
    dataset_sha256: str = Field(max_length=64)
    status: str = Field(default="QUEUED", index=True, max_length=24)
    sample_count: int = Field(default=0, ge=0)
    correct_count: int = Field(default=0, ge=0)
    review_count: int = Field(default=0, ge=0)
    accuracy: Optional[float] = Field(default=None, ge=0, le=1)
    results_json: str = Field(default="[]", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = Field(default=None)


class FeedbackTask(SQLModel, table=True):
    """A batch of wrong examples reviewed before being fed into a new round."""

    id: str = Field(default_factory=new_id, primary_key=True)
    round_id: str = Field(foreign_key="extractionround.id", index=True)
    model_connection_id: str = Field(foreign_key="modelconnection.id", index=True)
    job_id: Optional[str] = Field(default=None, foreign_key="job.id", index=True)
    name: str = Field(max_length=160)
    task_type: str = Field(default="CLASSIFICATION", max_length=24)
    status: str = Field(default="DRAFT", index=True, max_length=24)
    source_filename: str = Field(default="", max_length=255)
    source_path: str = Field(default="", sa_column=Column(Text))
    source_sha256: str = Field(default="", max_length=64)
    cases_json: str = Field(default="[]", sa_column=Column(Text))
    promoted_round_id: Optional[str] = Field(default=None, foreign_key="extractionround.id", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

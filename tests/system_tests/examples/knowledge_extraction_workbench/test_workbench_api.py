from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "examples"))

from knowledge_extraction_workbench.backend.app import APP_STORE, create_app
from knowledge_extraction_workbench.backend.config import Settings
from knowledge_extraction_workbench.backend.errors import WorkbenchError
from knowledge_extraction_workbench.backend.models import Job
from knowledge_extraction_workbench.backend.pipeline import DeterministicTestModel


def make_settings(root: Path) -> Settings:
    return Settings(
        data_dir=root,
        database_path=root / "workbench.sqlite3",
        upload_dir=root / "uploads",
        asset_dir=root / "assets",
        skill_dir=root / "skills",
        key_path=root / "master.key",
        frontend_dist=root / "dist",
        max_upload_bytes=5 * 1024 * 1024,
    )


async def wait_for_job(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        body = await response.json()
        if body["status"] in {"COMPLETED", "FAILED"}:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def create_test_app(root: Path):
    return create_app(make_settings(root), test_model=DeterministicTestModel())


@pytest.mark.asyncio
async def test_complete_injected_model_workflow_and_immutable_publish(tmp_path):
    app = create_test_app(tmp_path)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        create_response = await client.post(
            "/api/v1/scenes",
            json={
                "name": "差旅费用审核",
                "description": "从制度和案例中萃取审核知识",
                "goal": "形成规则、流程、QA 和评测集",
                "subscenes": ["申请前审核", "报销复核"],
            },
        )
        assert create_response.status == 201
        created = await create_response.json()
        scene_id = created["scene"]["id"]
        round_id = created["round"]["id"]

        update_response = await client.patch(
            f"/api/v1/scenes/{scene_id}",
            json={"name": "差旅费用审核（已确认）", "subscenes": ["申请前审核", "报销复核"]},
        )
        assert update_response.status == 200, await update_response.text()
        assert (await update_response.json())["name"] == "差旅费用审核（已确认）"

        material_text = "\n".join(
            [
                "当员工提交差旅申请时，必须填写出差目的、预算和成本中心；预算超过一万元时由部门负责人复核。",
                "财务审核人员应核对发票抬头、金额和行程日期；资料不完整时退回申请人补充，不得直接通过。",
                "国际差旅需要额外提交邀请函和合规说明，涉及敏感地区时转交合规负责人进行人工确认。",
            ]
            * 18
        )
        form = FormData()
        form.add_field("file", material_text.encode(), filename="travel-policy.txt", content_type="text/plain")
        upload_response = await client.post(f"/api/v1/rounds/{round_id}/materials", data=form)
        assert upload_response.status == 201, await upload_response.text()
        uploaded = await upload_response.json()
        assert uploaded["sha256"]

        extract_response = await client.post(f"/api/v1/rounds/{round_id}/extract")
        assert extract_response.status == 202, await extract_response.text()
        extract_job = await extract_response.json()
        extract_result = await wait_for_job(client, extract_job["id"])
        assert extract_result["status"] == "COMPLETED", extract_result

        event_response = await client.get(f"/api/v1/jobs/{extract_job['id']}/events?after=0")
        assert event_response.status == 200
        event_text = await event_response.text()
        assert "event: progress" in event_text
        assert '"phase": "completed"' in event_text

        document_response = await client.get(f"/api/v1/rounds/{round_id}/document")
        document = await document_response.json()
        assert document["structured"]["rules"]
        assert "travel-policy.txt" in document["markdown"]

        missing_instruction_response = await client.post(
            f"/api/v1/rounds/{round_id}/suggestions",
            json={"mode": "CUSTOM", "instruction": ""},
        )
        assert missing_instruction_response.status == 422
        assert (await missing_instruction_response.json())["code"] == "SUGGESTION_INSTRUCTION_REQUIRED"

        suggestion_response = await client.post(
            f"/api/v1/rounds/{round_id}/suggestions",
            json={"mode": "CUSTOM", "instruction": "补充复核留痕与异常升级条件"},
        )
        assert suggestion_response.status == 201
        suggestion = await suggestion_response.json()
        assert "按意图改写" in suggestion["explanation"]
        apply_response = await client.post(
            f"/api/v1/suggestions/{suggestion['id']}/apply",
            json={"base_revision": document["revision"]},
        )
        assert apply_response.status == 200
        applied_document = await apply_response.json()
        assert applied_document["revision"] == document["revision"] + 1
        assert "复核要求" in applied_document["markdown"]

        stale_suggestion_response = await client.post(f"/api/v1/rounds/{round_id}/suggestions")
        stale_suggestion = await stale_suggestion_response.json()
        save_response = await client.put(
            f"/api/v1/rounds/{round_id}/document",
            json={
                "markdown": applied_document["markdown"] + "\n手工复核备注。\n",
                "base_revision": applied_document["revision"],
                "reason": "手工复核",
            },
        )
        saved_document = await save_response.json()
        conflict_response = await client.post(
            f"/api/v1/suggestions/{stale_suggestion['id']}/apply",
            json={"base_revision": stale_suggestion["base_revision"]},
        )
        assert conflict_response.status == 409
        assert (await conflict_response.json())["code"] == "DOCUMENT_REVISION_CONFLICT"

        asset_response = await client.post(f"/api/v1/rounds/{round_id}/assets", json={})
        assert asset_response.status == 202, await asset_response.text()
        asset_job = await asset_response.json()
        asset_result = await wait_for_job(client, asset_job["id"])
        assert asset_result["status"] == "COMPLETED", asset_result
        assets_response = await client.get(f"/api/v1/rounds/{round_id}/assets")
        asset_payload = await assets_response.json()
        assert asset_payload["complete"] is True
        assert {item["kind"] for item in asset_payload["items"]} == {
            "RULES_XLSX",
            "THOUGHT_CHAIN_MD",
            "SKILL_ZIP",
            "QA_JSONL",
            "EVAL_JSONL",
        }
        assert next(item for item in asset_payload["items"] if item["kind"] == "EVAL_JSONL")["synthetic"] is True

        download_response = await client.get(asset_payload["items"][0]["download_url"])
        assert download_response.status == 200
        assert await download_response.read()

        previews = {}
        for asset in asset_payload["items"]:
            preview_response = await client.get(asset["preview_url"])
            assert preview_response.status == 200, await preview_response.text()
            previews[asset["kind"]] = await preview_response.json()
        assert previews["RULES_XLSX"]["mode"] == "table"
        assert previews["RULES_XLSX"]["columns"][0] == "规则ID"
        assert previews["RULES_XLSX"]["rows"]
        assert previews["THOUGHT_CHAIN_MD"]["mode"] == "markdown"
        assert "决策研判链" in previews["THOUGHT_CHAIN_MD"]["text"]
        assert previews["SKILL_ZIP"]["mode"] == "archive"
        assert "SKILL.md" in {entry["path"] for entry in previews["SKILL_ZIP"]["entries"]}
        assert previews["QA_JSONL"]["items"]
        assert previews["EVAL_JSONL"]["items"]

        bundle_response = await client.get(f"/api/v1/rounds/{round_id}/assets/download")
        assert bundle_response.status == 200, await bundle_response.text()
        assert bundle_response.content_type == "application/zip"
        with zipfile.ZipFile(io.BytesIO(await bundle_response.read())) as bundle:
            bundled_names = set(bundle.namelist())
            assert "manifest.json" in bundled_names
            assert {item["filename"] for item in asset_payload["items"]}.issubset(bundled_names)
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["document_revision"] == saved_document["revision"]

        publish_response = await client.post(f"/api/v1/rounds/{round_id}/publish")
        assert publish_response.status == 200
        assert (await publish_response.json())["status"] == "PUBLISHED"
        immutable_response = await client.put(
            f"/api/v1/rounds/{round_id}/document",
            json={"markdown": "changed", "base_revision": saved_document["revision"]},
        )
        assert immutable_response.status == 409
        assert (await immutable_response.json())["code"] == "ROUND_IMMUTABLE"

        runtime_model_response = await client.post(
            "/api/v1/models",
            json={
                "name": "评测模型",
                "provider": "DeepSeek",
                "api_base": "https://example.invalid/v1",
                "model_name": "evaluation-test-model",
                "api_key": "test-secret",
            },
        )
        assert runtime_model_response.status == 201
        runtime_model = await runtime_model_response.json()

        runtime_skills = await (await client.get("/api/v1/runtime/skills")).json()
        assert runtime_skills["items"][0]["round_id"] == round_id
        assert runtime_skills["items"][0]["evaluation_asset"]["kind"] == "EVAL_JSONL"

        tryout_response = await client.post(
            "/api/v1/runtime/tryouts",
            json={
                "round_id": round_id,
                "model_connection_id": runtime_model["id"],
                "input": "一笔资料不完整的差旅申请应如何处理？",
            },
        )
        assert tryout_response.status == 200, await tryout_response.text()
        assert (await tryout_response.json())["matched_rules"]

        tryout_form = FormData()
        tryout_form.add_field("round_id", round_id)
        tryout_form.add_field("model_connection_id", runtime_model["id"])
        tryout_form.add_field("input", "结合附件判断")
        tryout_form.add_field(
            "file",
            "资料不完整时应退回补充，不得直接通过。".encode(),
            filename="tryout-case.txt",
            content_type="text/plain",
        )
        tryout_upload_response = await client.post("/api/v1/runtime/tryouts/upload", data=tryout_form)
        assert tryout_upload_response.status == 200, await tryout_upload_response.text()

        custom_evaluation_form = FormData()
        custom_evaluation_form.add_field("round_id", round_id)
        custom_evaluation_form.add_field("model_connection_id", runtime_model["id"])
        custom_evaluation_form.add_field(
            "file",
            "input,expected\n资料不完整时如何处理,退回申请人补充\n".encode(),
            filename="custom-evaluation.csv",
            content_type="text/csv",
        )
        custom_evaluation_response = await client.post(
            "/api/v1/evaluations/upload",
            data=custom_evaluation_form,
        )
        assert custom_evaluation_response.status == 202, await custom_evaluation_response.text()
        custom_evaluation = await custom_evaluation_response.json()
        custom_result = await wait_for_job(client, custom_evaluation["job"]["id"])
        assert custom_result["status"] == "COMPLETED", custom_result
        custom_snapshot = await (
            await client.get(f"/api/v1/evaluations/{custom_evaluation['evaluation']['id']}")
        ).json()
        assert custom_snapshot["dataset_kind"] == "UPLOADED"
        assert custom_snapshot["sample_count"] == 1

        evaluation_response = await client.post(
            "/api/v1/evaluations",
            json={"round_id": round_id, "model_connection_id": runtime_model["id"]},
        )
        assert evaluation_response.status == 202, await evaluation_response.text()
        evaluation_payload = await evaluation_response.json()
        evaluation_result = await wait_for_job(client, evaluation_payload["job"]["id"])
        assert evaluation_result["status"] == "COMPLETED", evaluation_result
        evaluation = await (
            await client.get(f"/api/v1/evaluations/{evaluation_payload['evaluation']['id']}")
        ).json()
        assert evaluation["sample_count"] > 1
        assert len(evaluation["results"]) == evaluation["sample_count"]
        assert evaluation["accuracy"] is not None
        assert evaluation["wrong_count"] > 0

        feedback_response = await client.post(
            f"/api/v1/evaluations/{evaluation['id']}/feedback",
            json={"task_type": "CLASSIFICATION"},
        )
        assert feedback_response.status == 201, await feedback_response.text()
        feedback = await feedback_response.json()
        assert feedback["case_count"] == evaluation["wrong_count"]

        analyze_response = await client.post(f"/api/v1/feedback-tasks/{feedback['id']}/analyze")
        assert analyze_response.status == 202, await analyze_response.text()
        analyze_payload = await analyze_response.json()
        analyze_result = await wait_for_job(client, analyze_payload["job"]["id"])
        assert analyze_result["status"] == "COMPLETED", analyze_result
        reviewed = await (await client.get(f"/api/v1/feedback-tasks/{feedback['id']}")).json()
        assert reviewed["status"] == "REVIEW"
        assert reviewed["cases"][0]["analysis"]["knowledge_gap"]

        reviewed_cases = [{**item, "expert_confirmed": True} for item in reviewed["cases"]]
        save_feedback_response = await client.put(
            f"/api/v1/feedback-tasks/{feedback['id']}",
            json={"cases": reviewed_cases},
        )
        assert save_feedback_response.status == 200
        assert (await save_feedback_response.json())["status"] == "READY"

        json_export = await client.get(f"/api/v1/feedback-tasks/{feedback['id']}/export?format=json")
        xlsx_export = await client.get(f"/api/v1/feedback-tasks/{feedback['id']}/export?format=xlsx")
        assert json_export.status == 200 and await json_export.read()
        assert xlsx_export.status == 200 and await xlsx_export.read()

        promote_response = await client.post(f"/api/v1/feedback-tasks/{feedback['id']}/promote")
        assert promote_response.status == 200, await promote_response.text()
        promoted = await promote_response.json()
        next_round = promoted["round"]
        assert next_round["version"] == 2
        copied_materials = await (await client.get(f"/api/v1/rounds/{next_round['id']}/materials")).json()
        assert len(copied_materials["items"]) == 2
        assert {item["role"] for item in copied_materials["items"]} == {"REFERENCE", "FEEDBACK"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_archive_conflict_and_model_secret_never_returns(tmp_path):
    app = create_test_app(tmp_path)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        created = await (await client.post("/api/v1/scenes", json={"name": "运行中场景"})).json()
        scene_id = created["scene"]["id"]
        round_id = created["round"]["id"]
        store = app[APP_STORE]
        with store.session() as session:
            session.add(Job(kind="EXTRACTION", status="RUNNING", scene_id=scene_id, round_id=round_id))
            session.commit()

        conflict = await client.delete(f"/api/v1/scenes/{scene_id}")
        assert conflict.status == 409
        assert (await conflict.json())["code"] == "SCENE_JOB_CONFLICT"

        model_response = await client.post(
            "/api/v1/models",
            json={
                "name": "测试 DeepSeek",
                "provider": "DeepSeek",
                "api_base": "https://example.invalid/v1",
                "model_name": "test-model",
                "api_key": "secret-value-must-not-return",
            },
        )
        assert model_response.status == 201
        model = await model_response.json()
        assert model["has_api_key"] is True
        assert "api_key" not in model
        assert "secret-value-must-not-return" not in json.dumps(model)

        listed = await (await client.get("/api/v1/models")).json()
        assert "secret-value-must-not-return" not in json.dumps(listed)

        with store.session() as session:
            running = session.exec(select(Job).where(Job.scene_id == scene_id)).first()
            assert running is not None
            running_id = running.id
        store.fail_job(
            running_id,
            WorkbenchError("MODEL_JSON_INVALID", "模型输出无效。", status=422, retryable=True),
        )
        failed = store.get(Job, running_id)
        assert failed.status == "FAILED"
        assert failed.phase == "failed"
        assert failed.error_code == "MODEL_JSON_INVALID"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_model_connection_test_passes_strict_tls_config(tmp_path, monkeypatch):
    captured = {}

    class StubModel:
        def __init__(self, client_config, request_config):
            captured["client_config"] = client_config
            captured["request_config"] = request_config

        async def invoke(self, *args, **kwargs):
            return None

    monkeypatch.setattr("openjiuwen.core.foundation.llm.Model", StubModel)
    app = create_test_app(tmp_path)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        created = await client.post(
            "/api/v1/models",
            json={
                "name": "测试 DeepSeek",
                "provider": "DeepSeek",
                "api_base": "https://api.deepseek.com/v1",
                "model_name": "deepseek-v4-flash",
                "api_key": "test-key",
            },
        )
        model = await created.json()

        tested = await client.post(f"/api/v1/models/{model['id']}/test")

        assert tested.status == 200, await tested.text()
        assert captured["client_config"].verify_ssl is True
        assert Path(captured["client_config"].ssl_cert).is_file()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_model_ability_scopes_and_skill_instance_versions(tmp_path):
    app = create_test_app(tmp_path)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        models = await (await client.get("/api/v1/models")).json()
        assert models["items"] == []

        skills = await (await client.get("/api/v1/skills")).json()
        templates = skills["items"]
        assert len(templates) == 7
        assert "错例分析与回流 Skill" in {item["name"] for item in templates}
        assert {item["kind"] for item in templates} == {"TEMPLATE"}
        assert all(item["read_only"] for item in templates)

        deepseek_response = await client.post(
            "/api/v1/models",
            json={
                "name": "DeepSeek",
                "provider": "DeepSeek",
                "api_base": "https://api.deepseek.com/v1",
                "model_name": "deepseek-chat",
                "api_key": "test-key",
            },
        )
        deepseek_model = await deepseek_response.json()
        openai_response = await client.post(
            "/api/v1/models",
            json={
                "name": "OpenAI 兼容连接",
                "provider": "OpenAI",
                "api_base": "https://api.openai.com/v1",
                "model_name": "gpt-4.1-mini",
                "api_key": "another-test-key",
            },
        )
        openai_model = await openai_response.json()

        ambiguous_reset = await client.post("/api/v1/ability-mounts/defaults")
        assert ambiguous_reset.status == 422
        assert (await ambiguous_reset.json())["code"] == "MODEL_SELECTION_REQUIRED"

        reset = await client.post(
            "/api/v1/ability-mounts/defaults",
            json={"model_connection_id": openai_model["id"]},
        )
        assert reset.status == 200, await reset.text()
        mounts = (await (await client.get("/api/v1/ability-mounts")).json())["items"]
        assert len(mounts) == 7
        assert sum(item["stage"] == "EXTRACTION" for item in mounts) == 2
        assert sum(item["stage"] == "GENERATION" for item in mounts) == 5
        assert {item["model_connection_id"] for item in mounts} == {openai_model["id"]}
        assert {item["model"]["provider"] for item in mounts} == {"OpenAI"}
        assert deepseek_model["id"] != openai_model["id"]

        created = await (
            await client.post(
                "/api/v1/scenes",
                json={"name": "差旅审核", "subscenes": ["报销复核"]},
            )
        ).json()
        scene_scope = f"SCENE:{created['scene']['id']}"
        scopes = (await (await client.get("/api/v1/ability-scopes")).json())["items"]
        assert {item["kind"] for item in scopes} == {"GLOBAL", "SCENE", "SUBSCENE"}
        scoped_mounts = (
            await (
                await client.get("/api/v1/ability-mounts", params={"scope_key": scene_scope})
            ).json()
        )["items"]
        assert all(item["inherited"] for item in scoped_mounts)
        override = await client.put(
            f"/api/v1/ability-mounts/{scoped_mounts[0]['id']}",
            json={"scope_key": scene_scope, "enabled": False},
        )
        assert override.status == 200
        assert (await override.json())["inherited"] is False

        instance_response = await client.post(
            "/api/v1/skills/instances",
            json={
                "template_id": templates[0]["id"],
                "name": "差旅规则萃取",
                "scene_name": "差旅审核",
                "notes": "首版",
            },
        )
        assert instance_response.status == 201, await instance_response.text()
        instance = await instance_response.json()
        assert instance["kind"] == "INSTANCE"
        assert instance["source_skill_id"] == templates[0]["id"]

        download = await client.get(instance["download_url"])
        assert download.status == 200
        with zipfile.ZipFile(io.BytesIO(await download.read())) as archive:
            assert {"SKILL.md", "scripts/validate.py"}.issubset(archive.namelist())

        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(
                "SKILL.md",
                "---\nname: travel-rule-extraction\ndescription: Travel rule extraction.\n---\n",
            )
            archive.writestr("references/README.md", "reviewed sources")
        version_form = FormData()
        version_form.add_field(
            "file",
            package.getvalue(),
            filename="travel-skill.zip",
            content_type="application/zip",
        )
        version_response = await client.post(
            f"/api/v1/skills/{instance['id']}/versions",
            data=version_form,
        )
        assert version_response.status == 201, await version_response.text()
        next_version = await version_response.json()
        assert next_version["version"] == "0.2.0"
        versions = await (
            await client.get(f"/api/v1/skills/{next_version['id']}/versions")
        ).json()
        assert [item["version"] for item in versions["items"]] == ["0.2.0", "0.1.0"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_exploration_upload_analyze_candidate_and_archive(tmp_path):
    app = create_test_app(tmp_path)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        create_response = await client.post(
            "/api/v1/explorations",
            json={"name": "制度场景探索", "goal": "识别审批与异常处理场景"},
        )
        assert create_response.status == 201
        exploration = await create_response.json()

        material_text = (
            "当客户提交大额退款申请时，客服需要核对订单状态、支付渠道和退款原因，超过授权额度时提交主管复核。\n"
            "资料不完整时应退回补充；发现重复退款或异常账户时，必须暂停自动处理并转交风险人员人工确认。\n"
        ) * 20
        form = FormData()
        form.add_field("file", material_text.encode(), filename="refund-policy.txt", content_type="text/plain")
        upload_response = await client.post(
            f"/api/v1/explorations/{exploration['id']}/materials",
            data=form,
        )
        assert upload_response.status == 201, await upload_response.text()

        analyze_response = await client.post(f"/api/v1/explorations/{exploration['id']}/analyze")
        assert analyze_response.status == 202
        job = await analyze_response.json()
        result = await wait_for_job(client, job["id"])
        assert result["status"] == "COMPLETED", result

        candidates = await (
            await client.get(f"/api/v1/explorations/{exploration['id']}/candidates")
        ).json()
        assert candidates["items"]
        candidate = candidates["items"][0]
        scene_response = await client.post(
            f"/api/v1/explorations/{exploration['id']}/candidates/{candidate['id']}/create-scene"
        )
        assert scene_response.status == 201
        assert (await scene_response.json())["scene_id"]

        archive_response = await client.delete(f"/api/v1/explorations/{exploration['id']}")
        assert archive_response.status == 200
        visible = await (await client.get("/api/v1/explorations")).json()
        assert not visible["items"]
    finally:
        await client.close()

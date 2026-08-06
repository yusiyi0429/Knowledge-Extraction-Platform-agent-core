from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "examples"))

from knowledge_extraction_workbench.backend.app import APP_STORE, create_app
from knowledge_extraction_workbench.backend.config import Settings
from knowledge_extraction_workbench.backend.models import Job


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


@pytest.mark.asyncio
async def test_complete_fake_model_workflow_and_immutable_publish(tmp_path):
    app = create_app(make_settings(tmp_path))
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

        suggestion_response = await client.post(f"/api/v1/rounds/{round_id}/suggestions")
        assert suggestion_response.status == 201
        suggestion = await suggestion_response.json()
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

        publish_response = await client.post(f"/api/v1/rounds/{round_id}/publish")
        assert publish_response.status == 200
        assert (await publish_response.json())["status"] == "PUBLISHED"
        immutable_response = await client.put(
            f"/api/v1/rounds/{round_id}/document",
            json={"markdown": "changed", "base_revision": saved_document["revision"]},
        )
        assert immutable_response.status == 409
        assert (await immutable_response.json())["code"] == "ROUND_IMMUTABLE"

        next_round_response = await client.post(f"/api/v1/scenes/{scene_id}/rounds")
        next_round = await next_round_response.json()
        assert next_round["version"] == 2
        copied_materials = await (await client.get(f"/api/v1/rounds/{next_round['id']}/materials")).json()
        assert len(copied_materials["items"]) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_archive_conflict_and_model_secret_never_returns(tmp_path):
    app = create_app(make_settings(tmp_path))
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
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_exploration_upload_analyze_candidate_and_archive(tmp_path):
    app = create_app(make_settings(tmp_path))
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

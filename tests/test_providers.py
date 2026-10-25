import base64
import io
import json
import threading
import time
import wave
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.app import create_app
from studio.models import MediaConfig, MediaRequest
from studio.providers import MediaAdapter, estimate, public_url, media_http_diagnostic, media_http_error
from studio.store import DomainError

KEY = "fixture-media-key-not-real"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    # Default transport refuses all network. Tests opt in to mock handlers only.
    app.state.media_adapter.transport = httpx.MockTransport(lambda r: pytest.fail("Unexpected media request"))
    with TestClient(app) as client:
        client.headers.update({"X-Studio-Token": client.get("/api/health").json()["token"]})
        yield client


def png():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(out, format="PNG")
    return out.getvalue()


def project(client, budget=10):
    response = client.post("/api/projects", headers={"X-Request-ID": "project-" + uuid.uuid4().hex},
                           json={"name": "媒体接口模拟测试", "budget": budget})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def configure(client, **kwargs):
    data = {"seedream": {"api_key": KEY}, "aliyun": {"api_key": KEY}}
    data.update(kwargs)
    response = client.put("/api/media-settings", json=data)
    assert response.status_code == 200, response.text
    return response.json()


def quotation(client, pid, value):
    response = client.post(f"/api/projects/{pid}/media-estimate", json=value)
    assert response.status_code == 200, response.text
    return response.json()


def payload(client, pid, value, max_cost=1):
    q = quotation(client, pid, value)
    return {**value, "input_refs": q["input_refs"], "max_cost": max_cost, "confirmed": True,
            "expected_config_version": q["config_version"], "expected_brief_revision": q["brief_revision"]}


def wait_job(client, pid, jid):
    for _ in range(150):
        job = next(j for j in client.get(f"/api/projects/{pid}/media-jobs").json() if j["id"] == jid)
        if job["status"] != "running":
            return job
        time.sleep(.02)
    pytest.fail("Mock media worker did not finish")


def upload(client, pid):
    return client.post(f"/api/projects/{pid}/assets", files={"file": ("reference.png", png(), "image/png")}).json()


def test_settings_no_network_secrets_and_relay_destination(client, tmp_path):
    assert not client.get("/api/media-settings").json()["seedream"]["has_key"]
    state = configure(client)
    assert state["seedream"]["ready"] and state["aliyun"]["ready"]
    assert KEY not in json.dumps(state) and "api_key" not in state["seedream"]
    assert not client.get("/api/capabilities").json()["audience_video_validation"]["enabled"]
    assert client.put("/api/media-settings", json={}).json()["seedream"]["has_key"]
    invalid = client.put("/api/media-settings", json={"seedream": {"api_key": KEY, "enabled": True}})
    assert invalid.status_code == 422 and KEY not in invalid.text
    invalid = client.put("/api/media-settings", json={"aliyun": {"api_key": KEY, "workspace_id": "invalid/"}})
    assert invalid.status_code == 422 and KEY not in invalid.text
    configure(client, seedance={"base_url": "https://example.com/api/v3", "model": "relay-seedance", "api_key": KEY})
    assert not client.get("/api/media-settings").json()["seedance"]["ready"]
    changed = client.put("/api/media-settings", json={"seedance": {"base_url": "https://other.example.com/api/v3"}})
    assert changed.status_code == 422 and "重新填写" in changed.text and KEY not in changed.text
    restored = create_app(tmp_path).state.media_settings.status()
    assert restored["seedream"]["has_key"] and restored["seedance"]["has_key"]
    clear = client.put("/api/media-settings", json={"seedream": {"clear_key": True}}).json()
    assert not clear["seedream"]["has_key"] and clear["aliyun"]["has_key"]


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost/api", "https://127.0.0.1/", "https://10.0.0.1/",
    "https://[::1]/", "https://user:password@example.com/", "https://example.com:8080/", "https://example.com/#secret"])
def test_forbid_local_or_credential_destinations(url):
    with pytest.raises(DomainError):
        public_url(url)


def test_price_and_parameter_boundaries(client):
    pid = project(client)
    a = upload(client, pid)
    q = quotation(client, pid, {"provider": "wan", "prompt": "风吹过船长衣角", "reference_ids": [a["id"]]})
    assert q["estimated_cost"] == .75
    assert q["input_refs"] == [{"kind": "asset", "id": a["id"], "version": 1}]
    assert quotation(client, pid, {"provider": "seedream", "prompt": "船长"})["estimated_cost"] == .12
    assert quotation(client, pid, {"provider": "cosyvoice", "prompt": "字" * 600})["estimated_cost"] == .12
    assert client.post(f"/api/projects/{pid}/media-estimate", json={"provider": "wan", "prompt": "船长"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/media-estimate", json={"provider": "seedream", "prompt": "船长", "seed": 1}).status_code == 422
    assert client.post(f"/api/projects/{pid}/media-estimate", json={"provider": "seedance", "prompt": "船长"}).status_code == 422
    other = project(client)
    assert client.post(f"/api/projects/{other}/media-estimate", json={"provider": "seedream", "prompt": "船长", "reference_ids": [a["id"]]}).status_code == 404
    configure(client, aliyun={"api_key": KEY, "tts_model": "cosyvoice-v3.5-plus"})
    invalid = client.post(f"/api/projects/{pid}/media-estimate", json={"provider": "cosyvoice", "prompt": "船长"})
    assert invalid.status_code == 422 and "没有系统音色" in invalid.text


def test_seedream_end_to_end_idempotency_and_billing(client, tmp_path):
    configure(client)
    pid = project(client)
    a = upload(client, pid)
    requests = []
    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/api/v3/images/generations" and request.headers["authorization"] == "Bearer " + KEY
        assert body["image"].startswith("data:image/png;base64,") and body["response_format"] == "b64_json"
        assert body["size"] == "864x1536" and "seed" not in body and "sequential_image_generation" not in body
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}], "usage": {"image_count": 1, "secret": KEY}})
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    value = payload(client, pid, {"provider": "seedream", "prompt": "船长肖像", "reference_ids": [a["id"]]}, .15)
    first = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "image-once"}, json=value)
    assert first.status_code == 200, first.text
    done = wait_job(client, pid, first.json()["id"])
    assert done["status"] == "completed" and done["actual_cost"] is None
    duplicate = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "image-once"}, json=value)
    assert duplicate.json()["id"] == done["id"] and len(requests) == 1
    snap = client.get(f"/api/projects/{pid}").json()
    generated = next(r for r in snap["records"] if r["id"] == done["result"]["asset_id"])
    assert generated["data"]["source"] == "api_generation" and generated["input_refs"][0]["id"] == a["id"]
    assert snap["balance"]["reserved"] == .15 and KEY not in json.dumps(snap)
    assert client.get(generated["data"]["url"]).content == png()
    reconciled = client.post(f"/api/projects/{pid}/media-jobs/{done['id']}/reconcile", json={"actual_cost": .12, "evidence": "模拟账单"})
    assert reconciled.status_code == 200
    balance = client.get(f"/api/projects/{pid}").json()["balance"]
    assert balance["reserved"] == 0 and balance["actual"] == .12
    assert KEY not in client.get(f"/api/projects/{pid}/media-jobs").text


def test_preflight_blocks_budget_and_changed_config_before_spending(client):
    configure(client)
    pid = project(client, budget=.1)
    body = payload(client, pid, {"provider": "seedream", "prompt": "船长"}, .15)
    result = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "budget"}, json=body)
    assert result.status_code == 409 and "预算不足" in result.text
    assert client.get(f"/api/projects/{pid}/media-jobs").json() == []
    pid = project(client)
    body = payload(client, pid, {"provider": "seedream", "prompt": "船长"}, .15)
    configure(client, seedream={"api_key": "another-fixture-key"})
    result = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "config-changed"}, json=body)
    assert result.status_code == 409 and "配置已改变" in result.text


def test_timeout_unknown_fee_never_retries(client, tmp_path):
    configure(client)
    pid = project(client)
    requests = []
    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout(KEY, request=request)
    client.app.state.media_adapter.transport = httpx.MockTransport(fail)
    value = payload(client, pid, {"provider": "seedream", "prompt": "船长"}, .15)
    response = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "timeout"}, json=value)
    done = wait_job(client, pid, response.json()["id"])
    assert done["status"] == "interrupted" and KEY not in done["error"]
    assert done["actual_cost"] is None and len(requests) == 1
    client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "timeout"}, json=value)
    assert len(requests) == 1 and client.get(f"/api/projects/{pid}").json()["balance"]["reserved"] == .15
    app = create_app(tmp_path)
    with app.state.store.connect() as db:
        assert app.state.media_jobs(db, pid)[0]["status"] == "interrupted"


@pytest.mark.parametrize("status,code,expected", [
    (404, "ModelNotOpen", "开通管理"),
    (404, "InvalidEndpointOrModel.NotFound", "推理接入点"),
    (404, "InvalidEndpointOrModel.ModelIDAccessDisabled", "不允许通过模型ID"),
    (401, "AuthenticationError", "Key缺失或无效"),
    (403, "OperationDenied.ServiceOverdue", "余额欠费"),
    (404, KEY, "接口、模型或任务不存在"),
    (401, None, "鉴权失败"),
    (429, None, "限流或配额限制"),
    (429, "SetLimitExceeded", "推理限额已达到"),
    (429, "ModelAccountIpmRateLimitExceeded", "每分钟图片数"),
    (429, "ServerOverloaded", "服务资源紧张"),
    (400, "MissingParameter", "缺少必要参数"),
    (400, "InvalidParameter", "请求参数不合法"),
    (400, "InvalidParameter.UnsupportedParameter", "不支持的参数"),
    (400, "InputTextSensitiveContentDetected", "输入文本审核未通过"),
    (400, "InputTextRiskDetection", "输入文本审核未通过"),
    (400, "InputImageSensitiveContentDetected", "参考图片审核未通过"),
    (400, "OutputImageSensitiveContentDetected", "输出审核"),
    (400, "OutputImageRiskDetection", "输出审核"),
    (400, "ContentSecurityDetectionError", "审核服务请求失败"),
    (400, None, "具体错误码未识别"),
])
def test_provider_errors_are_actionable_and_redacted(client, status, code, expected):
    configure(client)
    pid = project(client)
    submitted = []
    def fail(request):
        submitted.append(request)
        return httpx.Response(status, json={"error": {"code": code, "message": KEY,
            "request_id": KEY}, "private": KEY})
    client.app.state.media_adapter.transport = httpx.MockTransport(fail)
    value = payload(client, pid, {"provider": "seedream", "prompt": "船长定妆帧"}, .12)
    created = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "failed-image"}, json=value)
    done = wait_job(client, pid, created.json()["id"])
    assert done["status"] == "interrupted" and expected in done["error"]
    assert KEY not in done["error"] and "不会自动重试" in done["error"]
    assert done["remote_id"] is None and done["actual_cost"] is None
    assert len(submitted) == 1
    assert client.get(f"/api/projects/{pid}").json()["balance"]["reserved"] == .12
    assert done["result"]["provider_error"]["http_status"] == status
    assert KEY not in json.dumps(done["result"]["provider_error"])


@pytest.mark.parametrize("code,message,reason", [
    ("QuotaExceeded", "The account has exhausted its free trial quota", "free_quota"),
    ("QuotaExceeded", "Too many queued tasks", "queued_tasks"),
    ("QuotaExceeded", "The request has exceeded the quota", "quota_unspecified"),
    ("RequestBurstTooFast", "System protection triggered", "burst"),
    ("InflightBatchsizeExceeded", "Concurrent limit", "concurrency"),
])
def test_429_reason_and_support_id_are_preserved_without_body(code, message, reason):
    request = httpx.Request("POST", "https://ark.cn-beijing.volces.com/api/v3/images/generations",
                            headers={"Authorization":"Bearer " + KEY})
    response = httpx.Response(429, request=request, json={"error":{"code":code,
        "message":message + " secret=" + KEY + " Request id: support-request-123"}})
    diagnostic = media_http_diagnostic(response)
    assert diagnostic == {"http_status":429,"code":code,"reason":reason,"request_id":"support-request-123"}
    assert KEY not in media_http_error(response) and "support-request-123" in media_http_error(response)
    malicious = httpx.Response(429, request=request, headers={"x-request-id":KEY},
                               json={"error":{"code":KEY,"message":KEY}})
    assert media_http_diagnostic(malicious)["request_id"] is None
    assert KEY not in json.dumps(media_http_diagnostic(malicious))


@pytest.mark.parametrize("aspect,size", [("16:9", "1536x864"), ("9:16", "864x1536"), ("1:1", "1152x1152")])
def test_seedream_wire_body_matches_official_contract(aspect, size):
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert json.loads(request.content) == {
            "model": "doubao-seedream-5-0-flash-260915", "prompt": "船长三视图", "size": size,
            "response_format": "b64_json", "stream": False, "output_format": "png", "watermark": True,
        }
        width,height = map(int,size.split("x"))
        assert 921600 <= width*height <= 4624220
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(png()).decode()}]})
    result = MediaAdapter(httpx.MockTransport(respond)).generate(
        MediaRequest(provider="seedream",prompt="船长三视图"),
        MediaConfig(seedream={"api_key":KEY}),[],aspect,lambda *a: pytest.fail("No async task for image"))
    assert result["bytes"] == png() and len(requests) == 1


def test_wan_protocol_and_query_recovery_never_recreates(client):
    configure(client, aliyun={"api_key": KEY, "workspace_id": "workspace-test"})
    pid = project(client)
    a = upload(client, pid)
    requests = []
    queries = 0
    def respond(request):
        nonlocal queries
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert request.url.host == "workspace-test.cn-beijing.maas.aliyuncs.com"
            assert request.headers["x-dashscope-async"] == "enable"
            assert body["parameters"]["audio"] is False and body["parameters"]["resolution"] == "720P"
            assert body["parameters"]["seed"] == 123 and body["input"]["negative_prompt"] == "多余手指"
            return httpx.Response(200, json={"output": {"task_id": "wan-test-id", "task_status": "PENDING"}})
        queries += 1
        return httpx.Response(500, text=KEY)
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    body = payload(client, pid, {"provider": "wan", "prompt": "船长睁眼", "reference_ids": [a["id"]], "seed": 123, "negative_prompt": "多余手指"})
    response = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "wan"}, json=body)
    done = wait_job(client, pid, response.json()["id"])
    assert done["status"] == "interrupted" and done["remote_id"] == "wan-test-id" and KEY not in done["error"]
    client.post(f"/api/projects/{pid}/media-jobs/{done['id']}/retrieve", json={})
    wait_job(client, pid, done["id"])
    assert len([r for r in requests if r.method == "POST"]) == 1 and queries == 2


def test_cosyvoice_audio_download_is_unauthenticated_and_imported(client):
    configure(client)
    pid = project(client)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000); output.writeframes(b"\x00\x00" * 2400)
    requests = []
    def respond(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/services/audio/tts/SpeechSynthesizer")
            body = json.loads(request.content)
            assert body["input"] == {"text": "那天，我被一声枪响打醒了。", "voice": "longanyang", "format": "wav", "sample_rate": 24000}
            return httpx.Response(200, json={"request_id": "tts-fixture", "output": {"finish_reason": "stop",
                "audio": {"url": "http://fixture.oss-cn-beijing.aliyuncs.com/audio.wav?signed=fixture"}}, "usage": {"characters": 15}})
        assert request.url.scheme == "https" and "authorization" not in request.headers
        return httpx.Response(200, content=buffer.getvalue())
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    body = payload(client, pid, {"provider": "cosyvoice", "prompt": "那天，我被一声枪响打醒了。"}, .1)
    response = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "tts"}, json=body)
    done = wait_job(client, pid, response.json()["id"])
    assert done["status"] == "completed", done
    assert done["result"]["usage"] == {"characters": 15} and "download_url" not in done["result"]
    snap = client.get(f"/api/projects/{pid}").json()
    audio = next(r for r in snap["records"] if r["id"] == done["result"]["asset_id"])
    assert audio["data"]["media_type"] == "audio" and len(requests) == 2


def test_relay_requires_own_price_and_ark_compatible_contract():
    config = MediaConfig(seedance={"api_key": KEY, "base_url": "https://example.com/api/v3", "model": "relay-model", "protocol": "ark", "price_per_second": .7})
    assert estimate(MediaRequest(provider="seedance", prompt="船长"), config)["estimated_cost"] == 3.5
    requests = []
    def respond(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/v3/contents/generations/tasks"
            body = json.loads(request.content)
            assert body["model"] == "relay-model" and body["generate_audio"] is False and body["resolution"] == "720p"
            assert body["ratio"] == "adaptive"
            assert body["content"][1]["role"] == "first_frame"
            return httpx.Response(200, json={"id": "relay-job"})
        assert request.url.path == "/api/v3/contents/generations/tasks/relay-job"
        return httpx.Response(200, json={"status": "succeeded", "content": {"video_url": "https://example.com/clip.mp4"}, "usage": {"completion_tokens": 123}})
    adapter = MediaAdapter(httpx.MockTransport(respond))
    remote = []
    value = MediaRequest(provider="seedance", prompt="船长")
    assert adapter.generate(value, config, ["data:image/png;base64,fixture"], "16:9", lambda r, u, usage: remote.append(r)) is None
    assert remote == ["relay-job"]
    assert adapter.query("seedance", remote[0], config)["usage"] == {"completion_tokens": 123}
    assert len(requests) == 2


def test_generated_asset_retains_stale_input_history(client):
    configure(client)
    pid = project(client)
    a = upload(client, pid)
    entered, release = threading.Event(), threading.Event()
    def respond(request):
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    body = payload(client, pid, {"provider": "seedream", "prompt": "船长", "reference_ids": [a["id"]]}, .15)
    response = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "stale"}, json=body)
    assert entered.wait(2)
    edit = client.put(f"/api/projects/{pid}/assets/{a['id']}", json={"expected_version": 1, "name": "参考更新", "license": "人工记录"})
    assert edit.status_code == 200
    release.set()
    done = wait_job(client, pid, response.json()["id"])
    assert done["status"] == "completed" and done["stale"]
    asset = next(r for r in client.get(f"/api/projects/{pid}").json()["records"] if r["id"] == done["result"]["asset_id"])
    assert asset["stale"] and asset["input_refs"][0]["version"] == 1

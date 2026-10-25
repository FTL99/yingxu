import json
import subprocess

import httpx
import imageio_ffmpeg
import pytest

from studio.models import MediaConfig, MediaRequest
from studio.providers import MediaAdapter, MediaSettings, estimate
from studio.store import DomainError
from test_providers import client, configure, payload, project, upload, wait_job, KEY


BASE = "https://openrouter.ai/api/v1"
MODEL = "bytedance/seedance-2.0"
CATALOG = {"data": [{"id": MODEL, "supported_durations": list(range(4, 16)),
    "supported_resolutions": ["720p"], "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
    "supported_frame_images": ["first_frame"], "seed": True,
    "pricing_skus": {"video_tokens": "0.000007"}}]}


def relay():
    return {"base_url": BASE, "protocol": "openrouter", "model": MODEL, "api_key": KEY, "price_per_second": 1.01}


def catalog_response(request):
    if request.url.path.endswith("/key"):
        return httpx.Response(200, json={"data": {"disabled": False, "label": KEY}})
    return httpx.Response(200, json=CATALOG)


def test_openrouter_settings_and_read_only_check(client, tmp_path):
    pid = project(client)
    before = client.get(f"/api/projects/{pid}").json()["balance"]
    configure(client, seedance=relay())
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "openrouter.ai"
        assert request.headers["authorization"] == "Bearer " + KEY
        return catalog_response(request)
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    checked = client.post("/api/media-settings/seedance/check", json={})
    assert checked.status_code == 200 and checked.json()["authenticated"]
    assert checked.json()["supplier_currency"] == "USD" and KEY not in checked.text
    assert len(requests) == 2
    saved = MediaSettings(tmp_path).config()
    q = estimate(MediaRequest(provider="seedance", prompt="纸船", duration=4), saved)
    assert q["estimated_cost"] == 4.04 and "token" in q["note"]
    assert client.get(f"/api/projects/{pid}").json()["balance"] == before
    assert client.get(f"/api/projects/{pid}/media-jobs").json() == []


@pytest.mark.parametrize("change", [{"base_url": "https://example.com/api/v1"}, {"model": "seedance2"}])
def test_openrouter_rejects_wrong_destination_or_alias(client, change):
    response = client.put("/api/media-settings", json={"seedance": {**relay(), **change}})
    assert response.status_code == 422 and KEY not in response.text


@pytest.mark.parametrize("case", ["disabled", "missing_model", "unsupported_duration", "unsupported_frame"])
def test_openrouter_preflight_blocks_before_generation(case):
    config = MediaConfig(seedance=relay())
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"disabled": case == "disabled"}})
        data = json.loads(json.dumps(CATALOG))
        if case == "missing_model": data["data"] = []
        if case == "unsupported_duration": data["data"][0]["supported_durations"] = [4]
        if case == "unsupported_frame": data["data"][0]["supported_frame_images"] = []
        return httpx.Response(200, json=data)
    adapter = MediaAdapter(httpx.MockTransport(respond))
    request = MediaRequest(provider="seedance", prompt="纸船", reference_ids=["a"] if case == "unsupported_frame" else [])
    with pytest.raises(DomainError):
        adapter.generate(request, config, ["data:image/png;base64,fixture"] if request.reference_ids else [], "16:9", lambda *args: None)
    assert all(r.method == "GET" for r in requests)


def test_openrouter_complete_download_recovery_and_usd_usage(client, tmp_path):
    pid = project(client)
    configure(client, seedance=relay())
    image = upload(client, pid)
    clip = tmp_path / "fixture.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=24",
        "-t", "0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True, capture_output=True)
    requests, download_count = [], 0
    def respond(request):
        nonlocal download_count
        requests.append(request)
        assert request.url.host == "openrouter.ai", "Untrusted returned URLs must never receive credentials"
        assert request.headers["authorization"] == "Bearer " + KEY
        if request.url.path.endswith(("/key", "/models")): return catalog_response(request)
        if request.method == "POST":
            assert request.url.path == "/api/v1/videos"
            body = json.loads(request.content)
            assert body["aspect_ratio"] == "9:16" and body["generate_audio"] is False and body["resolution"] == "720p"
            assert body["frame_images"][0]["frame_type"] == "first_frame"
            assert body["seed"] == 7 and "content" not in body
            return httpx.Response(202, json={"id": "video-original", "polling_url": "https://attacker.example/poll", "status": "pending"})
        if request.url.path.endswith("/content"):
            assert request.url.params["index"] == "0"
            download_count += 1
            return httpx.Response(502) if download_count == 1 else httpx.Response(200, content=clip.read_bytes())
        assert request.url.path == "/api/v1/videos/video-original"
        return httpx.Response(200, json={"status": "completed", "unsigned_urls": ["https://attacker.example/clip"], "usage": {"cost": .45, "total_tokens": 5}})
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    body = payload(client, pid, {"provider": "seedance", "prompt": "纸船", "duration": 4, "seed": 7, "reference_ids": [image["id"]]}, 5)
    started = client.post(f"/api/projects/{pid}/media-jobs", json=body, headers={"X-Request-ID": "or-once"})
    interrupted = wait_job(client, pid, started.json()["id"])
    assert interrupted["status"] == "interrupted" and interrupted["remote_id"] == "video-original"
    assert interrupted["result"]["usage"]["cost_usd"] == .45
    assert interrupted["actual_cost"] is None and interrupted["reservation"] == 5
    recovered = client.post(f"/api/projects/{pid}/media-jobs/{interrupted['id']}/retrieve", json={})
    assert recovered.status_code == 200
    done = wait_job(client, pid, interrupted["id"])
    assert done["status"] == "completed", done
    assert sum(r.method == "POST" for r in requests) == 1
    assert done["result"]["usage"]["cost_usd"] == .45 and done["actual_cost"] is None
    assert "download_url" not in done["result"] and KEY not in json.dumps(done)
    snap = client.get(f"/api/projects/{pid}").json()
    asset = next(a for a in snap["records"] if a["id"] == done["result"]["asset_id"])
    assert asset["data"]["media_type"] == "video" and asset["data"]["human_accepted"] is False


def test_openrouter_download_redirect_drops_credentials(tmp_path):
    config = MediaConfig(seedance=relay())
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.host == "openrouter.ai":
            assert request.headers["authorization"] == "Bearer " + KEY
            return httpx.Response(302, headers={"Location": "https://example.com/video.mp4?signature=fixture"})
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"fixture-video")
    path = tmp_path / "clip.mp4"
    MediaAdapter(httpx.MockTransport(respond)).download_openrouter("video-id", config, path)
    assert path.read_bytes() == b"fixture-video" and len(requests) == 2


@pytest.mark.parametrize("status", ["pending", "in_progress", "failed", "cancelled", "expired", "unknown"])
def test_openrouter_query_states(status):
    adapter = MediaAdapter(httpx.MockTransport(lambda r: httpx.Response(200, json={"status": status})))
    config = MediaConfig(seedance=relay())
    if status in ("pending", "in_progress"):
        assert adapter.query("seedance", "video-id", config) is None
    else:
        with pytest.raises(DomainError): adapter.query("seedance", "video-id", config)

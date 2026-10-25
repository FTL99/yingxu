"""No paid/network tests: protocol mocks and decoded real local audio samples."""
import array
import json
import math
import subprocess
import time
from pathlib import Path

import httpx
import imageio_ffmpeg
import pytest

from studio.media import has_audio_stream, render_animatic
from studio.models import MediaConfig, MediaRequest
from studio.providers import MediaAdapter, MediaSettings, estimate
from studio.store import DomainError
from test_providers import client, configure, project, upload, payload, wait_job
from test_openrouter import relay, MODEL


def ffmpeg(*args):
    return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
                          capture_output=True, check=True, timeout=30).stdout


def tone(path, frequency, duration=.4, video=False):
    args = ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000"]
    if video:
        args += ["-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    ffmpeg(*args, "-t", duration, path)
    return path


def samples(path):
    return array.array("f", ffmpeg("-i", path, "-map", "0:a:0", "-ac", 1, "-ar", 48000, "-f", "f32le", "-"))


def energy(values, start, end):
    part = values[round(start*48000):round(end*48000)]
    return math.sqrt(sum(x*x for x in part)/len(part))


def band(values, frequency, start, end):
    part = values[round(start*48000):round(end*48000)]
    return abs(sum(x * complex(math.cos(2*math.pi*frequency*i/48000), math.sin(2*math.pi*frequency*i/48000))
                   for i, x in enumerate(part))) / len(part)


def test_source_sound_mute_hold_frame_and_narration_offset(tmp_path):
    video = tone(tmp_path / "tone.mp4", 440, video=True)
    voice = tone(tmp_path / "voice.wav", 880)
    assets = {"v": {"path": str(video), "media_type": "video"}, "n": {"path": str(voice), "media_type": "audio"}}
    shots = [{"id": "s", "version": 1, "data": {"asset_id": "v", "duration": 1, "audio_direction": "枪响后静默"}},
             {"id": "card", "version": 1, "data": {"duration": .5}}]
    result = render_animatic(tmp_path / "sound", {"aspect": "1:1"}, shots, assets, size=(64,64))
    values = samples(result["video"])
    assert energy(values, .08, .28) > .05
    assert energy(values, .6, .9) < .001, "Short sound must not loop with the video"
    assert energy(values, 1.1, 1.4) < .001, "Planning cards need silent padding"
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["shots"][0]["source_audio_used"] and not manifest["shots"][1]["source_has_audio"]
    assert manifest["shots"][0]["audio_direction"] == "枪响后静默"
    muted = render_animatic(tmp_path / "mute", {"aspect": "1:1"}, shots, assets, size=(64,64),
                            audio_options={"preserve_source_audio": False})
    assert energy(samples(muted["video"]), .1, .3) < .001
    mixed = render_animatic(tmp_path / "mix", {"aspect": "1:1"}, shots, assets, size=(64,64),
                            audio_options={"narration_asset_id": "n", "narration_start_seconds": .2, "source_gain": .5})
    values = samples(mixed["video"])
    assert band(values, 880, .05, .15) < .001, "Narration must wait for its offset"
    assert band(values, 880, .25, .35) > .025
    ratio = band(values, 440, .25, .35) / band(samples(result["video"]), 440, .25, .35)
    assert .45 < ratio < .55, "Source gain should halve the original tone while narration survives"
    assert energy(values, .7, .9) < .001
    assert max(abs(x) for x in values) < .96


def test_many_cuts_do_not_accumulate_audio_padding(tmp_path):
    shots = [{"id": f"s{i}", "version": 1, "data": {"duration": .5}} for i in range(12)]
    result = render_animatic(tmp_path / "cuts", {"aspect": "1:1"}, shots, {}, size=(32,32))
    assert abs(len(samples(result["video"]))/48000 - 6) < .03


def test_openrouter_sound_payload_capability_and_separate_price(tmp_path):
    config = MediaConfig(seedance={**relay(), "audio_price_per_second": 1.4})
    value = MediaRequest(provider="seedance", prompt="船舱闪光", duration=4, audio=True, audio_direction="第2秒一声火枪响，无对白、无音乐")
    assert estimate(value, config)["estimated_cost"] == 5.6
    silent = value.model_copy(update={"audio": False, "audio_direction": ""})
    assert estimate(silent, config)["estimated_cost"] == 4.04
    with pytest.raises(DomainError, match="有声视频预算"):
        estimate(value, MediaConfig(seedance=relay()))
    settings = MediaSettings(tmp_path)
    settings.save(config)
    settings.save(MediaConfig(seedance={"audio_price_per_second": 1.5}))
    assert settings.config().seedance.api_key == config.seedance.api_key
    assert settings.config().seedance.price_per_second == 1.01
    calls = []
    def response(request):
        calls.append(request)
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"disabled": False}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL, "generate_audio": True,
                "supported_durations": [4], "supported_resolutions": ["720p"], "supported_aspect_ratios": ["1:1"]}]})
        body = json.loads(request.content)
        assert body["generate_audio"] is True and value.audio_direction in body["prompt"]
        assert body["prompt"].startswith(value.prompt) and "audio_prompt" not in body
        return httpx.Response(202, json={"id": "sound-task"})
    MediaAdapter(httpx.MockTransport(response)).generate(value, config, [], "1:1", lambda *args: None)
    assert sum(r.method == "POST" for r in calls) == 1
    calls.clear()
    def unsupported(request):
        assert request.method == "GET", "Audio capability must be checked before spending"
        result = response(request)
        data = result.json()
        if "data" in data and isinstance(data["data"], list):
            data["data"][0].pop("generate_audio")
        return httpx.Response(200, json=data)
    with pytest.raises(DomainError, match="未明确支持生成声音"):
        MediaAdapter(httpx.MockTransport(unsupported)).generate(value, config, [], "1:1", lambda *args: None)
    assert all(r.method == "GET" for r in calls)


def test_wan_sound_direction_and_invalid_provider_combinations():
    value = MediaRequest(provider="wan", prompt="船舱", reference_ids=["a"], audio=True, audio_direction="海浪")
    def response(request):
        body = json.loads(request.content)
        assert body["parameters"]["audio"] is True and "海浪" in body["input"]["prompt"]
        return httpx.Response(200, json={"output": {"task_id": "wan-audio"}})
    MediaAdapter(httpx.MockTransport(response)).generate(value, MediaConfig(), ["fixture"], "1:1", lambda *args: None)
    for provider in ("seedream", "cosyvoice", "wan", "seedance"):
        with pytest.raises(DomainError, match="声音设计仅用于"):
            estimate(MediaRequest(provider=provider, prompt="x", audio_direction="sound"), MediaConfig())
    with pytest.raises(DomainError, match="方舟兼容"):
        estimate(MediaRequest(provider="seedance", prompt="x", audio=True), MediaConfig(seedance={**relay(), "protocol": "ark"}))


def test_generated_asset_records_audio_request_and_actual_stream(client, tmp_path):
    pid = project(client)
    configure(client, seedance={**relay(), "audio_price_per_second": 1.2})
    clip = tone(tmp_path / "generated.mp4", 440, video=True)
    def respond(request):
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"disabled": False}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL, "generate_audio": True,
                "supported_durations": [4], "supported_resolutions": ["720p"], "supported_aspect_ratios": ["9:16"]}]})
        if request.method == "POST":
            return httpx.Response(202, json={"id": "generated-sound"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=clip.read_bytes())
        return httpx.Response(200, json={"status": "completed", "usage": {"cost": .5}})
    client.app.state.media_adapter.transport = httpx.MockTransport(respond)
    value = {"provider": "seedance", "prompt": "船舱", "duration": 4, "audio": True, "audio_direction": "海浪，无对白"}
    body = payload(client, pid, value, 5)
    started = client.post(f"/api/projects/{pid}/media-jobs", headers={"X-Request-ID": "generated-audio"}, json=body)
    assert started.status_code == 200, started.text
    done = wait_job(client, pid, started.json()["id"])
    assert done["status"] == "completed", done
    asset = next(a for a in client.get(f"/api/projects/{pid}").json()["records"] if a["id"] == done["result"]["asset_id"])
    assert asset["data"]["audio_requested"] is True and asset["data"]["has_audio_stream"] is True
    assert asset["data"]["audio_direction"] == "海浪，无对白"
    assert asset["data"]["human_accepted"] is False


def test_export_narration_provenance_and_cross_project(client, tmp_path):
    pid = project(client)
    voice = tone(tmp_path / "voice.wav", 880)
    a = client.post(f"/api/projects/{pid}/assets", files={"file": ("voice.wav", voice.read_bytes(), "audio/wav")}).json()
    shot = client.post(f"/api/projects/{pid}/records/shot", headers={"X-Request-ID": "shot"},
                       json={"data": {"duration": .3, "description": "静态卡"}}).json()
    body = {"shot_ids": [shot["id"]], "narration_asset_id": a["id"], "narration_start_seconds": .05}
    p2 = project(client)
    foreign = client.post(f"/api/projects/{p2}/assets", files={"file": ("voice.wav", voice.read_bytes(), "audio/wav")}).json()
    assert client.post(f"/api/projects/{pid}/exports", headers={"X-Request-ID": "foreign"}, json={**body, "narration_asset_id": foreign["id"]}).status_code == 404
    assert client.post(f"/api/projects/{pid}/exports", headers={"X-Request-ID": "offset"}, json={**body, "narration_start_seconds": .3}).status_code == 422
    image = upload(client, pid)
    assert client.post(f"/api/projects/{pid}/exports", headers={"X-Request-ID": "image"}, json={**body, "narration_asset_id": image["id"]}).status_code == 422
    started = client.post(f"/api/projects/{pid}/exports", headers={"X-Request-ID": "mix"}, json=body)
    assert started.status_code == 200, started.text
    for _ in range(150):
        export = client.get(f"/api/projects/{pid}").json()["exports"][0]
        if export["status"] != "running": break
        time.sleep(.05)
    assert export["status"] == "completed", export
    assert {"kind": "asset", "id": a["id"], "version": 1} in export["input_refs"]
    assert has_audio_stream(export["data"]["video"])
    client.put(f"/api/projects/{pid}/assets/{a['id']}", json={"expected_version": 1, "name": "旁白修订", "license": "自有"})
    assert client.get(f"/api/projects/{pid}").json()["exports"][0]["stale"]

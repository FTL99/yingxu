"""Delegated generation tests use a scripted agent and mock media providers only."""
import base64
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.models import StoryboardReply
from studio.store import Store
from test_providers import png, KEY, wait_job
from test_sound import tone


class MediaRunner:
    def __init__(self):
        self.actions = [{"tool":"generate_media","action_id":"frame", "request":{"provider":"seedream","prompt":"船长风格帧"}}]
        self.contexts = []
        self.release = threading.Event()
        self.release.set()
    def status(self): return {"ready":True}
    def run(self,*args):
        self.contexts.append(args[-1])
        self.release.wait(5)
        return StoryboardReply(title="开场风格帧",summary="准备生成样张供你查看",body="我会先生成船长风格帧，再根据你的反馈调整。",media_actions=self.actions)


@pytest.fixture
def env(tmp_path):
    runner = MediaRunner()
    app = create_app(tmp_path, runner)
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.host == "ark.cn-beijing.volces.com"
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(png()).decode()}]})
    app.state.media_adapter.transport = httpx.MockTransport(respond)
    with TestClient(app) as client:
        client.headers["X-Studio-Token"] = client.get("/api/health").json()["token"]
        client.put("/api/media-settings",json={"seedream":{"api_key":KEY}})
        pid = client.post("/api/projects",json={"name":"分镜媒体工具测试","budget":100},headers={"X-Request-ID":"p"}).json()["id"]
        # Test-only G1 fixture isolates tool delegation from separately tested gate flow.
        store = Store(tmp_path)
        with store.transaction() as db:
            store.save_record(db,pid,"decision",{"stage":"G1","allow":True,"note":"isolated test fixture"})
        tid = client.post(f"/api/projects/{pid}/tasks",json={"role":"storyboard","goal":"生成样张"},headers={"X-Request-ID":"t"}).json()["id"]
        yield client, runner, requests, pid, tid, store


def send(client,pid,tid,rid="turn",**extra):
    data = {"message":"生成开场样张给我看","max_cost":1,"media_budget":10,**extra}
    response = client.post(f"/api/projects/{pid}/tasks/{tid}/chat",json=data,headers={"X-Request-ID":rid})
    assert response.status_code == 200,response.text
    return response.json()


def done(client,pid,tid):
    for _ in range(200):
        view = client.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
        if not view["running"]: return view
        time.sleep(.02)
    pytest.fail("Scripted discussion did not finish")


def test_direct_generation_preview_context_idempotency_and_finalize(env):
    c,runner,requests,pid,tid,store = env
    first = send(c,pid,tid)
    assert send(c,pid,tid)["id"] == first["id"]
    view = done(c,pid,tid)
    assert view["task"]["status"] == "discussing"
    assert "media_actions" not in view["messages"][-1]["result"]
    media_id = view["media_calls"][0]["media_job_id"]
    wait_job(c,pid,media_id)
    view = done(c,pid,tid)
    card = view["media_calls"][0]
    assert card["status"] == "completed" and card["chat_job_id"] == first["id"]
    assert card["result"]["asset"]["data"]["media_type"] == "image"
    assert "path" not in card["result"]["asset"]["data"]
    assert c.get(card["result"]["asset"]["data"]["url"]).content == png()
    assert card["result"]["job"]["source_task_id"] == tid
    assert c.get(f"/api/projects/{pid}").json()["balance"]["reserved"] == 1.12
    assert len(requests) == 1 and KEY not in json.dumps(view)
    assert runner.contexts[0]["tool_contract"] == "storyboard-media-v1"
    assert runner.contexts[0]["media_authorization"]["reservation_limit"] == 10
    assert {t["name"] for t in runner.contexts[0]["tools"]} == {"generate_media","generate_image_batch","get_media_result"}
    # Next turn can retrieve the same real result without another generation.
    runner.actions = [{"tool":"get_media_result","action_id":"read","job_id":media_id}]
    send(c,pid,tid,"read",message="把刚才的结果给我看")
    latest = done(c,pid,tid)
    assert latest["media_calls"][-1]["result"]["asset"]["id"] == card["result"]["asset"]["id"]
    assert len(requests) == 1
    assert runner.contexts[-1]["media_call_history"][0]["status"] == "completed"
    finalized = c.post(f"/api/projects/{pid}/tasks/{tid}/chat/finalize",headers={"X-Request-ID":"final"},json={"message_id":latest["messages"][-1]["id"]})
    assert finalized.status_code == 200,finalized.text
    assert "media_actions" not in finalized.json()["data"]


@pytest.mark.parametrize("case",["zero","finalize","other_role","project_budget","turn_budget","no_key"])
def test_generation_is_blocked_before_provider_call(env,case):
    c,runner,requests,pid,tid,store = env
    extra = {}
    if case == "zero": extra["media_budget"] = 0
    if case == "finalize": extra["mode"] = "finalize"
    if case == "other_role":
        tid = c.post(f"/api/projects/{pid}/tasks",json={"role":"director","goal":"不能生成"},headers={"X-Request-ID":"other"}).json()["id"]
    if case == "project_budget":
        with store.transaction() as db:
            row = db.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()
            data = json.loads(row[0]);data["budget"] = 1.05
            db.execute("UPDATE projects SET data=? WHERE id=?",(json.dumps(data),pid))
    if case == "turn_budget": extra["media_budget"] = .1
    if case == "no_key": c.put("/api/media-settings",json={"seedream":{"clear_key":True}})
    send(c,pid,tid,**extra)
    view = done(c,pid,tid)
    assert not requests and c.get(f"/api/projects/{pid}/media-jobs").json() == []
    assert view["media_calls"][0]["status"] == "interrupted"
    assert view["media_calls"][0]["error"]
    assert view["task"]["status"] == "discussing"


def test_per_turn_total_and_input_version_provenance(env):
    c,runner,requests,pid,tid,store = env
    response = c.post(f"/api/projects/{pid}/records/bible",json={"data":{"title":"船长","bible_type":"character","fixed_description":"旧帽子"}},headers={"X-Request-ID":"bible"})
    assert response.status_code == 200,response.text
    record = response.json()
    runner.actions = [*runner.actions,{"tool":"generate_media","action_id":"frame2","request":{"provider":"seedream","prompt":"第二帧"}}]
    first = send(c,pid,tid,media_budget=.2)
    view = done(c,pid,tid)
    wait_job(c,pid,view["media_calls"][0]["media_job_id"])
    view = done(c,pid,tid)
    assert len(requests) == 1 and len(view["media_calls"]) == 2
    assert "本轮素材预留" in view["media_calls"][1]["error"]
    job = view["media_calls"][0]["result"]["job"]
    assert job["source_chat_job_id"] == first["id"]
    assert {"kind":"bible","id":record["id"],"version":1} in job["input_refs"]
    edited = c.post(f"/api/projects/{pid}/records/bible",json={"id":record["id"],"expected_version":1,"data":{"title":"船长","bible_type":"character","fixed_description":"新帽子"}},headers={"X-Request-ID":"edit"})
    assert edited.status_code == 200,edited.text
    view = c.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
    assert view["media_calls"][0]["result"]["job"]["stale"]
    assert view["media_calls"][0]["result"]["asset"]["stale"]


def test_foreign_reference_and_foreign_result_are_rejected(env):
    c,runner,requests,pid,tid,store = env
    other = c.post("/api/projects",json={"name":"另一个项目","budget":100},headers={"X-Request-ID":"p2"}).json()["id"]
    image = c.post(f"/api/projects/{other}/assets",files={"file":("test.png",png(),"image/png")}).json()
    runner.actions[0]["request"]["reference_ids"] = [image["id"]]
    send(c,pid,tid)
    assert done(c,pid,tid)["media_calls"][0]["status"] == "interrupted"
    assert requests == []
    runner.actions = [{"tool":"get_media_result","action_id":"foreign","job_id":"missing-or-foreign"}]
    send(c,pid,tid,"inspect")
    assert done(c,pid,tid)["media_calls"][-1]["error"] == "媒体任务不存在"


def test_brief_changes_during_model_reply_prevent_generation(env):
    c,runner,requests,pid,tid,store = env
    runner.release.clear()
    send(c,pid,tid)
    for _ in range(50):
        if runner.contexts: break
        time.sleep(.01)
    project = c.get(f"/api/projects/{pid}").json()["project"]
    data = {k:project[k] for k in ("name","brief","audience","promise","action","followup","duration","aspect","budget","sample")}
    c.put(f"/api/projects/{pid}",json={**data,"brief":"修改了需求","expected_revision":1})
    runner.release.set()
    view = done(c,pid,tid)
    assert not requests and not view["media_calls"]
    assert view["task"]["status"] == "needs_review"


def test_restart_never_replays_planned_or_completed_actions(env):
    c,runner,requests,pid,tid,store = env
    first = send(c,pid,tid)
    view = done(c,pid,tid)
    wait_job(c,pid,view["media_calls"][0]["media_job_id"])
    # Simulate a crash window after journaling another intent, before submission.
    with store.transaction() as db:
        db.execute("INSERT INTO storyboard_tool_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("crash",pid,tid,first["id"],"crash-action","generate_media","{}","planned",None,None,None,"test"))
    restored = create_app(store.root,MediaRunner())
    restored.state.media_adapter.transport = httpx.MockTransport(lambda r:pytest.fail("Restart must not generate"))
    with TestClient(restored) as fresh:
        view = fresh.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
        assert view["media_calls"][0]["status"] == "completed"
        assert view["media_calls"][-1]["status"] == "interrupted"
    assert len(requests) == 1


def test_delegated_video_with_sound_returns_playable_asset(env,tmp_path):
    c,runner,requests,pid,tid,store = env
    model = "bytedance/seedance-2.0"
    c.put("/api/media-settings",json={"seedance":{"api_key":KEY,"base_url":"https://openrouter.ai/api/v1",
          "model":model,"protocol":"openrouter","price_per_second":1.01,"audio_price_per_second":1.2}})
    clip = tone(tmp_path / "video.mp4",440,video=True)
    def respond(request):
        requests.append(request)
        assert request.url.host == "openrouter.ai"
        if request.url.path.endswith("/key"):
            return httpx.Response(200,json={"data":{"disabled":False}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200,json={"data":[{"id":model,"generate_audio":True,"supported_durations":[4],
                "supported_resolutions":["720p"],"supported_aspect_ratios":["9:16"]}]})
        if request.method=="POST":
            body=json.loads(request.content)
            assert body["generate_audio"] and "海浪" in body["prompt"]
            return httpx.Response(202,json={"id":"video-fixture"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200,content=clip.read_bytes())
        return httpx.Response(200,json={"status":"completed","usage":{"cost":.45}})
    c.app.state.media_adapter.transport=httpx.MockTransport(respond)
    runner.actions=[{"tool":"generate_media","action_id":"video","request":{"provider":"seedance","prompt":"船舱闪光", "duration":4,"audio":True,"audio_direction":"海浪，无对白无音乐"}}]
    send(c,pid,tid)
    view=done(c,pid,tid)
    wait_job(c,pid,view["media_calls"][0]["media_job_id"])
    card=done(c,pid,tid)["media_calls"][0]
    assert card["status"]=="completed" and card["result"]["asset"]["data"]["media_type"]=="video"
    assert card["result"]["asset"]["data"]["has_audio_stream"]
    assert c.get(card["result"]["asset"]["data"]["url"]).content==clip.read_bytes()
    assert card["result"]["job"]["reservation"]==4.8
    assert card["result"]["job"]["result"]["usage"]["cost_usd"]==.45
    assert sum(r.method=="POST" for r in requests)==1


def test_provider_error_is_visible_and_never_reposted(env):
    c,runner,requests,pid,tid,store=env
    def respond(request):
        requests.append(request)
        return httpx.Response(502,json={"error":KEY})
    c.app.state.media_adapter.transport=httpx.MockTransport(respond)
    first=send(c,pid,tid)
    view=done(c,pid,tid)
    wait_job(c,pid,view["media_calls"][0]["media_job_id"])
    view=done(c,pid,tid)
    assert view["media_calls"][0]["status"]=="interrupted"
    assert "HTTP 502" in view["media_calls"][0]["result"]["job"]["error"]
    assert KEY not in json.dumps(view)
    assert send(c,pid,tid)["id"]==first["id"] and len(requests)==1
    assert c.get(f"/api/projects/{pid}").json()["balance"]["reserved"]==1.12

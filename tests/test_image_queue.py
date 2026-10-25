"""Queue tests never contact a real supplier or incur generation charges."""
import base64
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from test_storyboard_tools import env, send, done
from test_providers import png


def batch(runner,count=6):
    runner.actions = [{"tool":"generate_image_batch","action_id":"frames","title":"整段分镜总览",
                       "items":[{"name":f"镜头{i+1}","request":{"provider":"seedream","prompt":f"船长镜头{i+1}"}} for i in range(count)]}]


def wait_batch(c,pid,tid,predicate=lambda b:b["status"] == "completed"):
    for _ in range(500):
        view = c.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
        queues = [call["result"]["batch"] for call in view["media_calls"] if (call.get("result") or {}).get("batch")]
        if queues and predicate(queues[-1]): return queues[-1]
        time.sleep(.02)
    pytest.fail("Mock image batch did not reach expected state")


def test_six_images_run_sequentially_once_and_overview_is_real(env):
    c,r,requests,pid,tid,store = env
    batch(r)
    running,peak = 0,0
    lock = threading.Lock()
    def respond(request):
        nonlocal running,peak
        with lock:
            running += 1; peak = max(peak,running); requests.append(request)
        time.sleep(.03)
        with lock: running -= 1
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(png()).decode()}]})
    c.app.state.media_adapter.transport = httpx.MockTransport(respond)
    first = send(c,pid,tid)
    assert send(c,pid,tid)["id"] == first["id"]
    done(c,pid,tid)
    queue = wait_batch(c,pid,tid)
    assert peak == 1 and len(requests) == 6 and len(r.contexts) == 1
    assert queue["counts"]["completed"] == 6
    assert "context" not in queue and all("value" not in item for item in queue["items"])
    assert len({i["media_job_id"] for i in queue["items"]}) == 6
    for item in queue["items"]:
        assert item["result"]["job"]["source_chat_job_id"] == first["id"]
        assert item["result"]["asset"]["data"]["media_type"] == "image"
        assert c.get(item["result"]["asset"]["data"]["url"]).content == png()
    assert c.get(f"/api/projects/{pid}").json()["balance"]["reserved"] == pytest.approx(1.72)
    assert not c.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()["media_running"]


@pytest.mark.parametrize("case",["turn_budget","project_budget","zero","wrong_provider"])
def test_whole_batch_preflight_blocks_before_any_image(env,case):
    c,r,requests,pid,tid,store = env
    batch(r)
    extra = {}
    if case == "turn_budget": extra["media_budget"] = .5
    if case == "zero": extra["media_budget"] = 0
    if case == "wrong_provider": r.actions[0]["items"][-1]["request"]["provider"] = "wan"
    if case == "project_budget":
        with store.transaction() as db:
            data = json.loads(db.execute("SELECT data FROM projects WHERE id=?",(pid,)).fetchone()[0])
            data["budget"] = 1.5
            db.execute("UPDATE projects SET data=? WHERE id=?",(json.dumps(data),pid))
    send(c,pid,tid,**extra)
    view = done(c,pid,tid)
    assert view["media_calls"][0]["status"] == "interrupted"
    assert not requests and not c.get(f"/api/projects/{pid}/media-jobs").json()


def test_pause_resume_and_cancel_preserve_completed_items(env):
    c,r,requests,pid,tid,store = env
    batch(r,4)
    entered,release = threading.Event(),threading.Event()
    def respond(request):
        requests.append(request); entered.set(); release.wait(5)
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(png()).decode()}]})
    c.app.state.media_adapter.transport = httpx.MockTransport(respond)
    send(c,pid,tid); done(c,pid,tid)
    assert entered.wait(3)
    queue = wait_batch(c,pid,tid,lambda b:b["counts"]["running"] == 1)
    url = f"/api/projects/{pid}/image-batches/{queue['id']}"
    assert c.post(url+"/pause",json={}).status_code == 200
    release.set()
    paused = wait_batch(c,pid,tid,lambda b:b["status"] == "paused" and b["counts"]["completed"] == 1)
    time.sleep(.3)
    assert len(requests) == 1 and paused["counts"]["pending"] == 3
    latest = c.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
    assert c.post(f"/api/projects/{pid}/tasks/{tid}/chat/finalize",json={"message_id":latest["messages"][-1]["id"]},headers={"X-Request-ID":"final"}).status_code == 409
    assert c.post(url+"/resume",json={}).status_code == 200
    result = wait_batch(c,pid,tid)
    assert len(requests) == 4 and result["items"][0]["media_job_id"] == paused["items"][0]["media_job_id"]


def test_429_pauses_and_continue_only_submits_remaining(env):
    c,r,requests,pid,tid,store = env
    batch(r,3)
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429,json={"error":{"code":"ModelAccountIpmRateLimitExceeded","message":"rate limit"}})
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(png()).decode()}]})
    c.app.state.media_adapter.transport = httpx.MockTransport(respond)
    send(c,pid,tid); done(c,pid,tid)
    queue = wait_batch(c,pid,tid,lambda b:b["status"] == "paused")
    assert len(requests) == 1 and queue["counts"]["failed"] == 1 and queue["counts"]["pending"] == 2
    assert "429" in queue["items"][0]["error"]
    assert c.post(f"/api/projects/{pid}/image-batches/{queue['id']}/resume",json={}).status_code == 200
    queue = wait_batch(c,pid,tid)
    assert len(requests) == 3 and queue["counts"]["completed"] == 2 and queue["counts"]["failed"] == 1


def test_restart_pauses_pending_without_replaying_and_cancel_releases_scope(env):
    c,r,requests,pid,tid,store = env
    batch(r,3)
    send(c,pid,tid); done(c,pid,tid)
    queue = wait_batch(c,pid,tid,lambda b:b["counts"]["completed"] >= 1)
    c.app.state.image_queue_stop.set()
    time.sleep(.3)
    with store.transaction() as db:
        db.execute("UPDATE image_batches SET status='queued' WHERE id=?",(queue["id"],))
    count = len(requests)
    app = create_app(store.root,r)
    app.state.media_adapter.transport = c.app.state.media_adapter.transport
    with TestClient(app) as fresh:
        fresh.headers["X-Studio-Token"] = fresh.get("/api/health").json()["token"]
        paused = wait_batch(fresh,pid,tid,lambda b:b["status"] == "paused")
        time.sleep(.3)
        assert len(requests) == count
        url = f"/api/projects/{pid}/image-batches/{paused['id']}"
        assert fresh.post(url+"/cancel",json={}).status_code == 200
        view = fresh.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
        assert view["media_calls"][0]["result"]["batch"]["status"] == "cancelled"
        assert len(requests) == count


def test_foreign_queue_and_changed_configuration_cannot_continue(env):
    c,r,requests,pid,tid,store = env
    batch(r,3)
    c.app.state.media_adapter.transport = httpx.MockTransport(lambda req:httpx.Response(429,json={"error":{"code":"RateLimitExceeded"}}))
    send(c,pid,tid); done(c,pid,tid)
    queue = wait_batch(c,pid,tid,lambda b:b["status"] == "paused")
    other = c.post("/api/projects",json={"name":"other","budget":100},headers={"X-Request-ID":"other"}).json()["id"]
    assert c.post(f"/api/projects/{other}/image-batches/{queue['id']}/resume",json={}).status_code == 404
    c.put("/api/media-settings",json={"seedream":{"clear_key":True}})
    assert c.post(f"/api/projects/{pid}/image-batches/{queue['id']}/resume",json={}).status_code == 409

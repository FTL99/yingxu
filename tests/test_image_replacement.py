import io
from pathlib import Path

import pytest
from PIL import Image

from test_workflow import client, project, record
from test_storyboard_tools import env, send, done
from test_image_queue import batch, wait_batch
from studio.app import ref
from studio.store import dump


def image(color="gold",format="PNG"):
    out=io.BytesIO()
    Image.new("RGB",(64,48),color).save(out,format=format)
    return out.getvalue()


def upload(c,pid):
    response=c.post(f"/api/projects/{pid}/assets",files={"file":("original.png",image("green"),"image/png")})
    assert response.status_code==200,response.text
    return response.json()


def replace(c,pid,asset,content=None,name="replacement.png",version=None):
    return c.post(f"/api/projects/{pid}/assets/{asset['id']}/replace-image",
                  data={"expected_version":asset["version"] if version is None else version},
                  files={"file":(name,image() if content is None else content,"image/png")})


def test_replacement_preserves_slot_history_and_bill_marks_dependents_stale(client):
    pid=project(client)
    old=upload(client,pid)
    shot=record(client,pid,"shot",{"duration":2,"description":"镜头","asset_id":old["id"]})
    before=client.get(f"/api/projects/{pid}").json()["balance"]
    response=replace(client,pid,old)
    assert response.status_code==200,response.text
    new=response.json()
    assert new["id"]==old["id"] and new["version"]==2
    assert new["input_refs"]==[] and not new["stale"]
    assert new["data"]["source"]=="human_replacement"
    assert new["data"]["name"]==old["data"]["name"]
    assert new["data"]["uploaded_filename"]=="replacement.png"
    assert not new["data"]["human_accepted"]
    assert new["data"]["url"]!=old["data"]["url"]
    assert client.get(new["data"]["url"]).content==image()
    assert client.get(old["data"]["url"]).content==image("green")
    history=client.get(f"/api/projects/{pid}/records/asset/{old['id']}/history").json()
    assert [a["version"] for a in history]==[2,1]
    assert history[1]["data"]==old["data"]
    snapshot=client.get(f"/api/projects/{pid}").json()
    current=next(r for r in snapshot["records"] if r["id"]==shot["id"])
    assert current["stale"] and current["data"]["asset_id"]==new["id"]
    assert snapshot["balance"]==before and not snapshot["media_jobs"]


@pytest.mark.parametrize("content,name,status",[(b"not an image","bad.png",422),(b"","empty.jpg",422),(image(),"wrong.svg",422),(b"x"*(10*1024*1024+1),"big.png",413),(image(format="GIF"),"disguised.png",422)],ids=["invalid","empty","extension","large","disguised"])
def test_invalid_replacement_is_atomic_and_cleans_up(client,content,name,status):
    pid=project(client);old=upload(client,pid)
    folder=Path(old["data"]["path"]).parent
    before=set(folder.iterdir())
    assert replace(client,pid,old,content,name).status_code==status
    assert set(folder.iterdir())==before
    history=client.get(f"/api/projects/{pid}/records/asset/{old['id']}/history").json()
    assert len(history)==1 and history[0]["data"]==old["data"]


def test_version_conflict_and_cross_project_are_rejected(client):
    pid=project(client);old=upload(client,pid)
    assert replace(client,project(client),old).status_code==404
    assert replace(client,pid,old).status_code==200
    folder=Path(old["data"]["path"]).parent;before=set(folder.iterdir())
    assert replace(client,pid,old).status_code==409
    assert set(folder.iterdir())==before
    assert replace(client,pid,old,version=0).status_code==422


@pytest.mark.parametrize("status",["discussing","pending_review","accepted","running"])
def test_draft_visual_discussion_adopts_new_image_but_approved_and_running_work_invalidates(env,status):
    c,runner,requests,pid,tid,store=env
    old=upload(c,pid)
    with store.transaction() as db:
        dependent=store.save_record(db,pid,"asset",{**old["data"],"source":"api_generation"},refs=[ref(old)])
        artifact=store.save_record(db,pid,"artifact",{"title":"旧版视觉方案"},refs=[ref(old)])
        db.execute("UPDATE tasks SET status=?,input_refs=?,result_id=? WHERE id=?",
                   (status,dump([ref(old),ref(dependent)]),artifact["id"],tid))
    assert replace(c,pid,old).status_code==200
    with store.connect() as db:
        task=store.task(db,tid,pid)
        assert store.record(db,pid,"artifact",artifact["id"])["stale"]
        assert store.record(db,pid,"asset",dependent["id"])["stale"]
        if status in ("discussing","pending_review"):
            assert task["status"]=="discussing"
            assert task["input_refs"]==[{**ref(old),"version":2}]
            store.dependencies_ready(db,task)
        else:
            assert task["status"]==("input_changed" if status=="running" else "needs_review")
            assert task["input_refs"]==[ref(old),ref(dependent)]
    if status in ("discussing","pending_review"):
        runner.actions=[]
        send(c,pid,tid,rid="continue",message="使用我上传的新图继续讨论",media_budget=0)
        done(c,pid,tid)
        inputs=runner.contexts[-1]["inputs"]
        assert any(a["id"]==old["id"] and a["version"]==2 for a in inputs)
        assert not any(a["id"]==dependent["id"] for a in inputs)
        assert any("用户上传图片替换" in turn["content"] for turn in runner.contexts[-1]["conversation"])
    assert not requests


def test_batch_and_discussion_show_latest_uploaded_image_without_generation(env):
    c,runner,requests,pid,tid,store=env
    batch(runner,2)
    send(c,pid,tid);done(c,pid,tid)
    overview=wait_batch(c,pid,tid)
    old=overview["items"][0]["result"]["asset"]
    before=c.get(f"/api/projects/{pid}").json()["balance"]
    oldjob=overview["items"][0]["result"]["job"]
    assert replace(c,pid,old).status_code==200
    current=c.get(f"/api/projects/{pid}/tasks/{tid}/chat").json()
    new=current["media_calls"][0]["result"]["batch"]["items"][0]["result"]["asset"]
    assert new["id"]==old["id"] and new["version"]==2
    assert new["data"]["source"]=="human_replacement" and "provider" not in new["data"]
    assert c.get(new["data"]["url"]).content==image()
    assert c.get(old["data"]["url"]).content!=image()
    assert len(requests)==2
    after=c.get(f"/api/projects/{pid}").json()
    assert after["balance"]==before
    job=next(j for j in after["media_jobs"] if j["id"]==oldjob["id"])
    assert job["result"]==oldjob["result"] and job["actual_cost"]==oldjob["actual_cost"]

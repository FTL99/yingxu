"""Bounded, durable storyboard image batches. No model/key access or replay."""
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor

from .models import MediaRequest, MediaGenerate
from .store import DomainError, dump, uid, now


def register_image_queue(app, store):
    stop = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-queue")
    app.state.image_queue_stop = stop
    app.state.image_queue_executor = executor
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS image_batches(
            id TEXT PRIMARY KEY,project_id TEXT,task_id TEXT,chat_job_id TEXT,action_id TEXT,
            title TEXT,status TEXT,items TEXT,context TEXT,error TEXT,created_at TEXT,
            UNIQUE(chat_job_id,action_id))""")
        db.execute("UPDATE image_batches SET status='paused',error='工作台重启，队列已暂停；继续时只处理未提交图片。' WHERE status IN ('queued','running')")

    def get(db, pid, qid):
        row = db.execute("SELECT * FROM image_batches WHERE id=? AND project_id=?", (qid,pid)).fetchone()
        if not row:
            raise DomainError("图片队列不存在",404)
        value = dict(row)
        value["items"] = json.loads(value["items"])
        value["context"] = json.loads(value["context"])
        # Source linkage is committed with the media job, before external submission.
        for i, item in enumerate(value["items"]):
            linked = db.execute("SELECT id FROM media_jobs WHERE project_id=? AND source_chat_job_id=? AND source_action_id=?",
                                (pid,value["chat_job_id"],f"{qid}-{i}")).fetchone()
            if linked:
                item["media_job_id"] = linked["id"]
                job = app.state.get_media_job(db,pid,linked["id"])
                item["status"] = "completed" if job["status"] == "completed" else "running" if job["status"] == "running" else "failed"
                item["error"] = job["error"]
            elif item["status"] == "submitting" and value["status"] not in ("running","queued"):
                item["status"] = "failed"
                item["error"] = "提交过程曾中断；不会重放此图片，请核对原记录。"
        return value

    def view(db,pid,qid):
        queue = get(db,pid,qid)
        queue.pop("context")
        for item in queue["items"]:
            item.pop("value",None)
            if item.get("media_job_id"):
                item["result"] = app.state.storyboard_result(db,pid,item["media_job_id"])
        queue["counts"] = {s:sum(item["status"] == s for item in queue["items"])
                           for s in ("pending","submitting","running","completed","failed","cancelled")}
        return queue

    def active(db,pid,tid=None):
        return bool(db.execute("SELECT 1 FROM image_batches WHERE project_id=? AND status IN ('queued','running','paused')" +
                               (" AND task_id=?" if tid else ""), (pid,tid) if tid else (pid,)).fetchone())

    def used(db,pid):
        charges = [dict(r) for table in ("tasks","chat_jobs","media_jobs")
                   for r in db.execute(f"SELECT reservation,actual_cost FROM {table} WHERE project_id=?",(pid,))]
        return sum(c["reservation"] if c["actual_cost"] is None else c["actual_cost"] for c in charges)

    def guard(db,queue):
        pid,tid,jid = queue["project_id"],queue["task_id"],queue["chat_job_id"]
        task = store.task(db,tid,pid)
        chat = db.execute("SELECT status FROM chat_jobs WHERE id=? AND project_id=? AND task_id=?",(jid,pid,tid)).fetchone()
        context = queue["context"]
        if (task["role"] != "storyboard" or task["status"] not in ("discussing","running")
                or task.get("superseded_by") or task.get("merged_into") or not chat or chat["status"] != "completed"):
            raise DomainError("讨论或阶段状态已变化，队列暂停；请核对当前阶段。")
        if store.project(db,pid)["revision"] != context["revision"]:
            raise DomainError("Brief已变化，队列暂停；请按新版本重新安排剩余图片。")
        store.validate_refs(db,pid,context["refs"])
        total = db.execute("SELECT COALESCE(SUM(reservation),0) FROM media_jobs WHERE project_id=? AND source_chat_job_id=?",(pid,jid)).fetchone()[0]
        remaining = sum(item["value"]["max_cost"] for item in queue["items"] if item["status"] == "pending")
        if total + remaining > context["allowance"] + 1e-8:
            raise DomainError("剩余队列超过本轮素材额度，已暂停。")
        if used(db,pid) + remaining > store.project(db,pid)["budget"] + 1e-8:
            raise DomainError("项目剩余预算不足，队列已暂停；请核账或调整预算后继续。")
        for item in queue["items"]:
            if item["status"] == "pending":
                quotation = app.state.quote_media(pid,MediaRequest.model_validate({k:v for k,v in item["value"].items()
                      if k not in ("confirmed","max_cost","expected_config_version","expected_brief_revision")}))
                if quotation["config_version"] != item["value"]["expected_config_version"]:
                    raise DomainError("供应商配置已变化，队列暂停；需按新配置重新安排。")

    def save(db,queue):
        db.execute("UPDATE image_batches SET status=?,items=?,error=? WHERE id=?",
                   (queue["status"],dump(queue["items"]),queue["error"],queue["id"]))

    def create(pid,tid,jid,action,context):
        refs = [{"kind":r["kind"],"id":r["id"],"version":r["version"]} for r in context["inputs"]]
        items = []
        for entry in action.items:
            if entry.request.provider != "seedream":
                raise DomainError("批量图片队列仅支持已配置的Seedream生图接口。",422)
            request = entry.request.model_dump()
            request["input_refs"] = [*refs,*[r for r in request["input_refs"] if r not in refs]]
            quotation = app.state.quote_media(pid,MediaRequest.model_validate(request))
            amount = max(.001,math.ceil(quotation["estimated_cost"] * 10000) / 10000)
            value = MediaGenerate(**request,confirmed=True,max_cost=amount,expected_config_version=quotation["config_version"],
                                  expected_brief_revision=context["project"]["revision"])
            items.append({"name":entry.name,"status":"pending","media_job_id":None,"error":None,"value":value.model_dump()})
        with store.transaction() as db:
            if active(db,pid):
                raise DomainError("本项目已有图片队列，请完成或取消后再建立新队列。")
            total = sum(i["value"]["max_cost"] for i in items)
            previous = db.execute("SELECT COALESCE(SUM(reservation),0) FROM media_jobs WHERE project_id=? AND source_chat_job_id=?",(pid,jid)).fetchone()[0]
            if total + previous > context["media_authorization"]["reservation_limit"] + 1e-8:
                raise DomainError("整批图片超过本轮素材预留额度；未提交任何图片。")
            if used(db,pid) + total > store.project(db,pid)["budget"] + 1e-8:
                raise DomainError("项目剩余预算不足以完成整批图片；未提交任何图片。")
            qid = uid("batch")
            snapshot = {"refs":refs,"revision":context["project"]["revision"],"allowance":context["media_authorization"]["reservation_limit"]}
            db.execute("INSERT INTO image_batches VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (qid,pid,tid,jid,action.action_id,action.title,"queued",dump(items),dump(snapshot),None,now()))
            store.event(db,pid,"image_queue.created",{"queue_id":qid,"count":len(items),"estimated_reservation":total})
            return qid

    def worker(pid,qid):
        try:
            while not stop.is_set():
                with store.transaction() as db:
                    queue = get(db,pid,qid)
                    if queue["status"] not in ("queued","running"):
                        save(db,queue)
                        return
                    if any(i["status"] == "running" for i in queue["items"]):
                        save(db,queue)
                        waiting = True
                    else:
                        failed = [i for i in queue["items"] if i["status"] == "failed" and not i.get("failure_acknowledged")]
                        if failed:
                            queue["status"] = "paused"
                            queue["error"] = "图片生成失败，队列已暂停；请核对失败原因和费用，继续时仅生成未提交图片。"
                            save(db,queue)
                            return
                        pending = next((n for n,i in enumerate(queue["items"]) if i["status"] == "pending"),None)
                        if pending is None:
                            queue["status"] = "completed"
                            queue["error"] = None
                            save(db,queue)
                            store.event(db,pid,"image_queue.completed",{"queue_id":qid})
                            return
                        guard(db,queue)
                        waiting = db.execute("SELECT COUNT(*) FROM media_jobs WHERE status='running'").fetchone()[0] >= 2
                        if not waiting:
                            queue["status"] = "running"
                            save(db,queue)
                if waiting:
                    stop.wait(.25)
                    continue
                value = MediaGenerate.model_validate(queue["items"][pending]["value"])
                def submit_guard(db):
                    current = get(db,pid,qid)
                    if current["status"] != "running" or current["items"][pending]["status"] != "pending":
                        raise DomainError("队列已暂停或取消，未提交下一张图片。")
                    guard(db,current)
                    current["items"][pending]["status"] = "submitting"
                    save(db,current)
                # Atomic job + submitting mark. If interrupted, reconcile by source ID.
                app.state.generate_media(pid,value,f"image-queue-{qid}-{pending}",
                    origin={"task_id":queue["task_id"],"chat_job_id":queue["chat_job_id"],"action_id":f"{qid}-{pending}"},guard=submit_guard)
                stop.wait(.25)
        except Exception as exc:
            safe = exc.message if isinstance(exc,DomainError) else "队列执行中断，请核对原任务；不会自动重新生成。"
            with store.transaction() as db:
                queue = get(db,pid,qid)
                if queue["status"] in ("queued","running"):
                    queue["status"],queue["error"] = "paused",safe
                    save(db,queue)

    def start(pid,tid,jid):
        with store.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM image_batches WHERE project_id=? AND task_id=? AND chat_job_id=? AND status='queued'",(pid,tid,jid))]
        for qid in ids:
            executor.submit(worker,pid,qid)

    @app.post("/api/projects/{pid}/image-batches/{qid}/{command}")
    def control(pid:str,qid:str,command:str):
        launch = False
        with store.transaction() as db:
            queue = get(db,pid,qid)
            if command not in ("pause","resume","cancel"):
                raise DomainError("未知队列操作",422)
            if queue["status"] in ("completed","cancelled"):
                raise DomainError("队列已结束，不能继续执行。")
            if command == "resume":
                if queue["status"] != "paused":
                    raise DomainError("仅暂停的队列可以继续。")
                if any(i["status"] == "running" for i in queue["items"]):
                    raise DomainError("当前图片仍在生成，请等它结束后继续。")
                guard(db,queue)
                # Failed submissions remain visible and are never retried. Worker only
                # pauses for new failures, so acknowledge these when continuing.
                for item in queue["items"]:
                    if item["status"] == "failed":
                        item["failure_acknowledged"] = True
                queue["status"],queue["error"] = "queued",None
                launch = True
            elif command == "pause":
                queue["status"],queue["error"] = "paused","已暂停；已提交图片会继续取回，剩余图片等待继续。"
            else:
                queue["status"],queue["error"] = "cancelled","已取消剩余图片；已提交任务仍会保留结果与费用记录。"
                for item in queue["items"]:
                    if item["status"] == "pending": item["status"] = "cancelled"
            save(db,queue)
            store.event(db,pid,f"image_queue.{command}",{"queue_id":qid})
            result = view(db,pid,qid)
        if launch: executor.submit(worker,pid,qid)
        return result

    app.state.create_image_batch = create
    app.state.start_image_batches = start
    app.state.image_batch_view = view
    app.state.image_batch_active = active

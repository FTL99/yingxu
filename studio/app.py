import json
import os
import re
import secrets
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, UploadFile, File, Form, Header
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .models import *
from .store import Store, DomainError, uid, dump, now
from .prompts import ROLE_META, role_prompt, ROOT, presentation_rules
from .runtime import HarnessRuntime
from .media import render_animatic
from .results import read_output
from .chat import register_chat
from .capabilities import capabilities, CAPABILITY_RULES
from .gemini import GeminiSettings, GeminiVideoAdapter
from .providers import register_media
from .handoff import upstream_package, append_upstream_refs, discussion_snapshot


def ref(record):
    return {"kind": record["kind"], "id": record["id"], "version": record["version"]}


def create_app(data_root=None, runner=None):
    store = Store(Path(data_root or os.environ.get("SHORTFILM_DATA", str(ROOT / "data"))))
    runtime = runner or HarnessRuntime(store.root)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio")
    store.recover()
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,project_id TEXT,status TEXT,input_refs TEXT,data TEXT,error TEXT,created_at TEXT)")
        db.execute("UPDATE exports SET status='interrupted',error='导出进程中断；不会自动重跑' WHERE status='running'")
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.image_queue_stop.set()
        app.state.image_queue_executor.shutdown(wait=False, cancel_futures=True)
        executor.shutdown(wait=False, cancel_futures=True)
        app.state.media_executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="映序 · 短片制作工作台", lifespan=lifespan)
    app.state.store = store
    app.state.runtime = runtime
    gemini_settings = GeminiSettings(store.root)
    gemini_video = GeminiVideoAdapter(gemini_settings)
    app.state.gemini_video = gemini_video
    register_media(app, store)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # No CORS, LAN binding, or cross-origin mutation. DNS rebinding must not
        # turn a webpage into a caller of local credential/media endpoints.
        host = (request.url.hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"detail": "仅支持本机访问"}, 403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin not in ("http://127.0.0.1:8811", "http://localhost:8811", "http://127.0.0.1:5173", "http://localhost:5173"):
                return JSONResponse({"detail": "拒绝跨站写入"}, 403)
            if request.headers.get("x-studio-token") != token:
                return JSONResponse({"detail": "请刷新工作台后重试"}, 403)
        return await call_next(request)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": exc.message}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path in ("/api/gemini", "/api/media-settings"):
            # Pydantic's default errors echo invalid input, which could contain a key.
            return JSONResponse({"detail": "配置格式不正确，请检查模型、地址和参数。"}, 422)
        if "/media-" in request.url.path:
            return JSONResponse({"detail": "媒体请求参数无效，请重新估算并检查必填项。"}, 422)
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)

    @app.get("/api/health")
    def health():
        return {"status":"ok", "app":"shortfilm-studio", "version":"0.1.0", "token":token}

    @app.get("/api/roles")
    def roles():
        return [{"id":r,"name":m[1],"description":m[2],"prompt_version":role_prompt(r)["version"]} for r,m in ROLE_META.items()]

    @app.get("/api/capabilities")
    def capability_status():
        return {**capabilities(), "media_generation": {"providers": app.state.media_catalog(),
            "execution": "分镜讨论可在本轮素材预留额度内直接生成并展示；其他角色仍通过手动生成入口"}}

    @app.get("/api/prompts/{role}")
    def prompt(role: str):
        if role not in ROLES:
            raise DomainError("角色不存在",404)
        return role_prompt(role)

    @app.get("/api/runtime")
    def runtime_status():
        return runtime.status()

    @app.put("/api/runtime")
    def config(value: RuntimeConfig):
        if value.base_url and urlparse(value.base_url).scheme not in ("http","https"):
            raise DomainError("API 地址须为 http 或 https",422)
        return runtime.save(value)

    @app.get("/api/gemini")
    def gemini_status():
        return gemini_settings.status()

    @app.put("/api/gemini")
    def save_gemini(value: GeminiConfig):
        return gemini_settings.save(value)

    @app.post("/api/projects/{pid}/video-analysis")
    def analyze_video(pid: str, value: GeminiAnalysisInput):
        # Deliberately blocked before reading media or creating paid-work intent.
        gemini_video.require_enabled()
        raise DomainError("视频分析的任务与费用核账流程尚未启用", 409)

    @app.get("/api/projects")
    def projects():
        with store.connect() as db:
            return [store.project(db,r["id"]) for r in db.execute("SELECT id FROM projects ORDER BY created_at DESC")]

    @app.post("/api/projects")
    def create_project(value: ProjectInput, x_request_id: str = Header(default="")):
        with store.transaction() as db:
            def operation():
                pid=uid("project")
                db.execute("INSERT INTO projects VALUES(?,?,?,?)",(pid,1,dump(value.model_dump()),now()))
                store.event(db,pid,"project.created",{"name":value.name})
                return store.project(db,pid)
            return store.idempotent(db,"__workspace__","project.create",x_request_id,value.model_dump(),operation)

    @app.put("/api/projects/{pid}")
    def edit_project(pid: str, value: ProjectEdit):
        with store.transaction() as db:
            old=store.project(db,pid)
            if old["revision"] != value.expected_revision:
                raise DomainError("Brief已被修改，请刷新后合并")
            data=value.model_dump(exclude={"expected_revision"})
            db.execute("UPDATE projects SET revision=revision+1,data=? WHERE id=?",(dump(data),pid))
            tasks=store.tasks(db,pid)
            changed={("task",t["id"]) for t in tasks}
            for t in tasks:
                status="input_changed" if t["status"]=="running" else "needs_review"
                db.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?",(status,now(),t["id"]))
                if t["result_id"]:
                    db.execute("UPDATE records SET stale=1 WHERE project_id=? AND kind='artifact' AND id=?",(pid,t["result_id"]))
                    changed.add(("record","artifact",t["result_id"]))
            for r in store.records(db,pid):
                if r["kind"]=="decision":
                    db.execute("UPDATE records SET stale=1 WHERE project_id=? AND kind=? AND id=?",(pid,r["kind"],r["id"]))
            store.invalidate(db,pid,changed)
            store.event(db,pid,"brief.updated",{"revision":old["revision"]+1})
            return store.project(db,pid)

    def balance(db,pid):
        tasks=store.tasks(db,pid)
        charges=tasks+[dict(r) for r in db.execute('SELECT reservation,actual_cost FROM chat_jobs WHERE project_id=?',(pid,))]
        charges += [dict(r) for r in db.execute('SELECT reservation,actual_cost FROM media_jobs WHERE project_id=?',(pid,))]
        return {"budget":store.project(db,pid)["budget"],"reserved":sum(t["reservation"] for t in charges if t["actual_cost"] is None),
                "actual":sum(t["actual_cost"] or 0 for t in charges),"unsettled":sum(t["reservation"]>0 and t["actual_cost"] is None for t in charges)}

    def gate(db,pid,stage):
        return any(r["kind"]=="decision" and not r["stale"] and r["data"].get("stage")==stage and r["data"].get("allow") for r in store.records(db,pid))

    def gate_record(db,pid,stage):
        return next((r for r in store.records(db,pid,"decision") if not r["stale"] and r["data"].get("stage")==stage and r["data"].get("allow")),None)

    def ready(db,task):
        store.dependencies_ready(db,task)
        required={"writer":"G0","assets":"G1","storyboard":"G1","post":"G2"}.get(task["role"])
        if required and not gate(db,task["project_id"],required):
            raise DomainError(f"阶段 {required} 尚未放行，请先评审并记录决定")

    @app.get("/api/projects/{pid}")
    def snapshot(pid: str):
        with store.connect() as db:
            project=store.project(db,pid)
            tasks=store.tasks(db,pid)
            for t in tasks:
                try:
                    ready(db,t)
                    t["blocker"]=""
                except DomainError as e:
                    t["blocker"]=e.message
            exports=[]
            for row in db.execute("SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC",(pid,)):
                e={**dict(row),"data":json.loads(row["data"]),"input_refs":json.loads(row["input_refs"])}
                try:
                    store.validate_refs(db,pid,e["input_refs"])
                    e["stale"]=e["data"].get("brief_revision")!=project["revision"]
                except DomainError:
                    e["stale"]=True
                exports.append(e)
            return {"project":project,"records":store.records(db,pid),"tasks":tasks,"balance":balance(db,pid),"exports":exports,"capabilities":capability_status(),
                    "media_jobs":app.state.media_jobs(db,pid),
                    "events":[{**dict(r),"data":json.loads(r["data"])} for r in db.execute("SELECT * FROM events WHERE project_id=? ORDER BY seq DESC LIMIT 80",(pid,))]}

    def validate_data(kind,data):
        if kind=="hypothesis":
            if not data.get("claim") or not data.get("owner"):
                raise DomainError("假设须填写陈述与负责人",422)
            if data.get("evidence_status","untested") not in ("untested","supported","contradicted","inconclusive"):
                raise DomainError("无效的假设证据状态",422)
            if data.get("source_type") not in ("model_inference","user_intuition","reference_observation","audience_feedback","behavior_data","tool_test"):
                raise DomainError("假设须注明有效的来源类型",422)
            if data.get("evidence_status","untested")!="untested" and (not data.get("evidence") or data.get("source_type") not in ("audience_feedback","behavior_data","tool_test")):
                raise DomainError("证据结论须注明真实测试或行为数据；模型推演、直觉和参考不能替代验证",422)
        if kind=="reference" and not data.get("source"):
            raise DomainError("参考须注明来源",422)
        if kind=="bible" and (data.get("bible_type") not in ("character","scene","style") or not data.get("title") or not data.get("fixed_description")):
            raise DomainError("资产 Bible 须注明类型、名称与固定描述",422)
        if kind=="shot":
            try:
                duration=float(data.get("duration",0))
            except (TypeError,ValueError):
                raise DomainError("镜头时长无效",422)
            if not 0 < duration <=60 or not data.get("description"):
                raise DomainError("镜头须有描述与 0–60 秒的时长",422)

    def validate_stage(db,pid,data,refs,record_id=None):
        stage=data.get("stage")
        if stage not in ("G0","G1","G2","G3") or not data.get("note") or not refs:
            raise DomainError("阶段决定须有阶段、理由及版本证据",422)
        if data.get("allow"):
            p=store.project(db,pid)
            if stage=="G0" and not all(p.get(k) for k in ("audience","promise","followup")):
                raise DomainError("请先补齐目标观众、内容承诺和后续价值",422)
            hypotheses=[r for r in store.records(db,pid,"hypothesis") if not r["stale"]]
            if not hypotheses:
                raise DomainError("请至少登记一条关键假设",422)
            risky=any(r["data"].get("evidence_status","untested")!="supported" for r in hypotheses)
            if risky and not data.get("risks_acknowledged"):
                raise DomainError("仍有未支持的假设，需明确记录接受的风险",422)
            prev={"G1":"G0","G2":"G1","G3":"G2"}.get(stage)
            if prev and not gate(db,pid,prev):
                raise DomainError(f"请先完成 {prev} 放行")
            if prev:
                refs.append(ref(gate_record(db,pid,prev)))
            refs.extend(ref(h) for h in hypotheses if ref(h) not in refs)
            required={"G0":"audience","G1":"writer","G2":"storyboard","G3":"post"}[stage]
            tasks=store.tasks(db,pid)
            accepted_roles={required}
            compact_directors={t['merged_into'] for t in tasks if t['role']=='audience' and t.get('merged_into')}
            if stage=='G0': accepted_roles.add('director')
            if not any(t["role"] in accepted_roles and (t['role']!='director' or t['id'] in compact_directors) and t["status"]=="accepted" and t["result_id"] in [r["id"] for r in refs if r["kind"]=="artifact"] for t in tasks):
                raise DomainError("放行须引用对应角色已验收的成果版本",422)
            if stage=="G3":
                if not any(store.record(db,pid,r["kind"],r["id"])["data"].get("final_movie") for r in refs if r["kind"]=="artifact"):
                    raise DomainError("动态分镜不能作为最终成片放行；请附真实成片和评审",422)
            # Track accepted work on the active gate itself so that
            # changing/revoking a gate invalidates downstream results.
        # One decision per stage, versioned; revocation propagates.
        existing=[r for r in store.records(db,pid,"decision") if r["data"].get("stage")==stage]
        if existing and not record_id:
            raise DomainError("阶段已有决定，请编辑原决定并保留历史")

    @app.post("/api/projects/{pid}/records/{kind}")
    def save_record(pid: str, kind: str, value: RecordInput, x_request_id: str=Header(default="")):
        if kind not in KINDS or kind in ("review","asset","artifact"):
            raise DomainError("请使用对应的成果、验收或素材入口",422)
        validate_data(kind,value.data)
        with store.transaction() as db:
            def operation():
                refs=[r.model_dump() for r in value.input_refs]
                if kind=="shot" and value.data.get("asset_id"):
                    asset=store.record(db,pid,"asset",value.data["asset_id"])
                    refs.append(ref(asset))
                if kind=="decision":
                    validate_stage(db,pid,value.data,refs,value.id)
                return store.save_record(db,pid,kind,value.data,value.id,value.expected_version,refs)
            return store.idempotent(db,pid,f"record.{kind}",x_request_id,value.model_dump(),operation)

    def make_task(db,pid,value):
        project=store.project(db,pid)
        for dep in value.dependencies:
            store.task(db,dep,pid)
        refs=[r.model_dump() for r in value.input_refs]
        store.validate_refs(db,pid,refs)
        tid=uid("task")
        db.execute("INSERT INTO tasks(id,project_id,role,goal,dependencies,input_refs,brief_revision,prompt_version,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (tid,pid,value.role,value.goal,dump(value.dependencies),dump(refs),project["revision"],role_prompt(value.role)["version"],"draft",now(),now()))
        store.event(db,pid,"task.created",{"task_id":tid,"role":value.role})
        return store.task(db,tid)

    @app.post("/api/projects/{pid}/tasks")
    def create_task(pid: str,value:TaskInput,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            return store.idempotent(db,pid,"task.create",x_request_id,value.model_dump(),lambda:make_task(db,pid,value))

    @app.post("/api/projects/{pid}/pipeline")
    def pipeline(pid:str,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            def operation():
                existing=store.tasks(db,pid)
                if existing:
                    raise DomainError("已有任务，请按需要建立局部返工任务")
                created={}
                for role in ("director","audience","writer","assets","storyboard","post"):
                    dependencies={"director":[],"audience":["director"],"writer":["audience"],"assets":["writer"],"storyboard":["writer","assets"],"post":["storyboard","assets"]}[role]
                    created[role]=make_task(db,pid,TaskInput(role=role,goal=ROLE_META[role][2],dependencies=[created[r]["id"] for r in dependencies]))
                return list(created.values())
            return store.idempotent(db,pid,"pipeline.create",x_request_id,{},operation)

    def submit(db,task,result,expected_version,source,final_asset_id=None):
        pid=task["project_id"]
        if task.get("superseded_by"):
            raise DomainError("任务已被替代，请使用新的角色任务")
        if task.get("merged_into"):
            raise DomainError("此角色已由总控承接，请使用总控的交接成果")
        if task["status"] in ("running","input_changed") and source=="human":
            raise DomainError("任务仍在执行，待执行结束或中断核对后再提交")
        ready(db,task)
        refs=list(task["input_refs"])
        package=upstream_package(store,db,task)
        append_upstream_refs(refs,package)
        for dep in task["dependencies"]:
            upstream=store.task(db,dep,pid)
            refs.append(ref(store.record(db,pid,"artifact",upstream["result_id"])))
        stage={"writer":"G0","assets":"G1","storyboard":"G1","post":"G2"}.get(task["role"])
        if stage:
            refs.append(ref(gate_record(db,pid,stage)))
        final_asset=None
        if final_asset_id:
            if source!="human" or task["role"]!="post":
                raise DomainError("最终成片须由后期任务提交真实视频素材",422)
            final_asset=store.record(db,pid,"asset",final_asset_id)
            if final_asset["data"]["media_type"]!="video":
                raise DomainError("最终成片素材必须为视频",422)
            refs.append(ref(final_asset))
        artifact=store.save_record(db,pid,"artifact",{**result.model_dump(),"discussion_snapshot":discussion_snapshot(db,task) if source=='role_chat' else [],"source":source,"role":task["role"],"task_id":task["id"],"prompt_version":task["prompt_version"],"brief_revision":task["brief_revision"],"final_movie":bool(final_asset),"final_asset_id":final_asset_id,"video_url":final_asset["data"]["url"] if final_asset else None},task["result_id"],expected_version,refs,skip_task=task["id"])
        db.execute("UPDATE tasks SET input_refs=? WHERE id=?",(dump(refs),task["id"]))
        db.execute("UPDATE tasks SET status='pending_review',result_id=?,error=NULL,updated_at=? WHERE id=?",(artifact["id"],now(),task["id"]))
        store.event(db,pid,"task.submitted",{"task_id":task["id"],"artifact":ref(artifact),"source":source})
        return artifact

    @app.post("/api/projects/{pid}/tasks/{tid}/submit")
    def manual_submit(pid:str,tid:str,value:SubmitInput,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            return store.idempotent(db,pid,"task.submit",x_request_id,{"tid":tid,**value.model_dump()},lambda:submit(db,store.task(db,tid,pid),RoleResult.model_validate(value.model_dump(exclude={"expected_version","final_asset_id"})),value.expected_version,"human",value.final_asset_id))

    @app.get("/api/projects/{pid}/tasks/{tid}/output")
    def task_output(pid:str,tid:str):
        with store.connect() as db:
            task=store.task(db,tid,pid)
        return read_output(store.root,task)

    @app.post("/api/projects/{pid}/tasks/{tid}/retry")
    def prepare_retry(pid:str,tid:str,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            def operation():
                old=store.task(db,tid,pid)
                if old["status"] not in ("interrupted","changes_requested") or old.get("superseded_by"):
                    raise DomainError("仅未被替代的中断或退回任务可重新生成")
                store.dependencies_ready(db,old)
                downstream=[t for t in store.tasks(db,pid) if tid in t["dependencies"]]
                if any(t["status"]!="draft" or t["session_id"] or t["result_id"] for t in downstream):
                    raise DomainError("已有下游执行记录，请建立局部返工任务并重新评审")
                replacement=make_task(db,pid,TaskInput(role=old["role"],goal=old["goal"],dependencies=old["dependencies"],input_refs=old["input_refs"]))
                db.execute("UPDATE tasks SET superseded_by=? WHERE id=?",(replacement["id"],tid))
                for child in downstream:
                    deps=[replacement["id"] if d==tid else d for d in child["dependencies"]]
                    db.execute("UPDATE tasks SET dependencies=?,updated_at=? WHERE id=?",(dump(deps),now(),child["id"]))
                store.event(db,pid,"task.retry_prepared",{"previous_task":tid,"replacement_task":replacement["id"],"rewired_drafts":[t["id"] for t in downstream],"previous_cost_retained":True})
                return replacement
            return store.idempotent(db,pid,"task.retry",x_request_id,{"tid":tid},operation)

    def review_task(db,task,value):
        tid=task["id"]
        pid=task["project_id"]
        if task["status"]!="pending_review":
            raise DomainError("仅待评审成果可以验收；输入变化须先重提交")
        ready(db,task)
        artifact=store.record(db,pid,"artifact",task["result_id"])
        store.validate_refs(db,pid,[ref(artifact)])
        for check in value.checks:
            refs=[r.model_dump() for r in check.evidence_refs]
            store.validate_refs(db,pid,refs)
            if ref(artifact) not in refs:
                raise DomainError("每项检查须引用本次成果的准确版本",422)
        if value.outcome=="accepted" and (any(c.status!="passed" for c in value.checks) or (artifact["data"].get("uncertainties") and not value.acknowledged_uncertainties)):
            raise DomainError("存在失败、未验证或未确认的不确定项，不能验收通过",422)
        if value.outcome=="accepted" and any(not c.note.strip() for c in value.checks):
            raise DomainError("验收须填写具体观察证据或位置",422)
        if value.outcome=="accepted" and artifact["data"].get("final_movie"):
            required={"画面与一致性","声音与节奏","字幕与信息","时长与画幅","目标行为与后续价值"}
            if not required.issubset({c.criterion for c in value.checks}):
                raise DomainError("最终成片须完成画面、声音、字幕、规格和目标行为五项人工检查",422)
        record=store.save_record(db,pid,"review",{**value.model_dump(),"reviewer":"human","task_id":tid},refs=[ref(artifact)])
        db.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?",(value.outcome,now(),tid))
        store.event(db,pid,"task.reviewed",{"task_id":tid,"outcome":value.outcome,"review_id":record["id"]})
        return record

    @app.post("/api/projects/{pid}/tasks/{tid}/review")
    def review(pid:str,tid:str,value:ReviewInput,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            def operation():
                return review_task(db,store.task(db,tid,pid),value)
            return store.idempotent(db,pid,"task.review",x_request_id,{"tid":tid,**value.model_dump()},operation)

    def model_worker(pid,tid,session_id,prompt,context):
        try:
            result=runtime.run(tid,session_id,prompt,context)
            with store.transaction() as db:
                task=store.task(db,tid,pid)
                if task["status"]!="running":
                    raise DomainError("执行期间输入发生变化，原会话结果需核对后重新提交")
                old=store.record(db,pid,"artifact",task["result_id"])["version"] if task["result_id"] else 0
                submit(db,task,result,old,"deepseek_harness")
        except Exception as error:
            # Exception strings from SDK/network layers can contain credentials.
            # Keep only safe domain errors, and an exception class otherwise.
            message=error.message if isinstance(error,DomainError) else f"{type(error).__name__}：运行失败或结果未符合数据合同。核对 Harness 会话后处理；不会自动重试。"
            with store.transaction() as db:
                db.execute("UPDATE tasks SET status='interrupted',error=?,updated_at=? WHERE id=?",(message,now(),tid))
                store.event(db,pid,"task.interrupted",{"task_id":tid,"message":message})

    @app.post("/api/projects/{pid}/tasks/{tid}/run")
    def run_task(pid:str,tid:str,value:StartTask,x_request_id:str=Header(default="")):
        # Persist intent/reservation before dispatch. A replay never launches a
        # second external call; interrupted calls are not retried automatically.
        launch=None
        with store.transaction() as db:
            def operation():
                nonlocal launch
                task=store.task(db,tid,pid)
                if task.get("superseded_by"):
                    raise DomainError("任务已被替代，请使用新的角色任务")
                if task["status"]!="draft":
                    raise DomainError("只能运行新建任务；返工请建立新任务，避免重复扣费")
                ready(db,task)
                if not runtime.status()["ready"]:
                    raise DomainError("请先配置并启用 DeepSeek Harness",422)
                b=balance(db,pid)
                if b["reserved"]+b["actual"]+value.max_cost>b["budget"]:
                    raise DomainError("项目余额不足，无法预留本次额度")
                prompt=role_prompt(task["role"])
                if prompt["version"]!=task["prompt_version"]:
                    raise DomainError("提示词已更新，请新建任务")
                refs=list(task["input_refs"])
                package=upstream_package(store,db,task)
                append_upstream_refs(refs,package)
                for dep in task["dependencies"]:
                    refs.append(ref(store.record(db,pid,"artifact",store.task(db,dep,pid)["result_id"])))
                records=[store.record(db,pid,r["kind"],r["id"],r["version"]) for r in refs]
                # Project-level references are snapshotted, versioned and tracked.
                contextual=[r for r in store.records(db,pid) if r["kind"] in ("hypothesis","reference","experiment","decision","lesson","bible","asset","shot") and not r["stale"]]
                refs+= [ref(r) for r in contextual if ref(r) not in refs]
                context={"project":{**store.project(db,pid),"budget_currency":"CNY"},"task":task,"inputs":records,"context_records":contextual,"tools":[],"media_providers":app.state.media_catalog(),"output_contract":RoleResult.model_json_schema(),
                         "capabilities":capabilities(),
                         "handoff_package":package,
                         "workflow_snapshot":[{k:t[k] for k in ("id","role","status","dependencies","result_id","superseded_by")} for t in store.tasks(db,pid)],
                         "phase_contract":{"G0":"已验收观众策略成果与假设记录","G1":"已验收编剧成果","G2":"已验收分镜成果","G3":"真实成片与人工五项检查"},
                         "response_rules":["预算以人民币元记账，不要换成USD或臆测供应商价格；本次模型调用费用由账单核对，不能宣称无费用。","输出一份简洁、完整的JSON：summary最多500字，body最多4500字，uncertainties最多8条，proposed_records最多3条。不要展开全部下游细节。","只描述本角色职责。若本角色是导演，首次交付聚焦观众、已有方向的取舍、高损失假设和下一步，供新手做一个具体决定。","以workflow_snapshot为真实任务状态，不要宣称已有角色尚未建立；沿用phase_contract的G0至G3，不另造门禁。"]}
                session_id=uid("session")
                db.execute("UPDATE tasks SET status='running',input_refs=?,session_id=?,reservation=?,updated_at=? WHERE id=?",(dump(refs),session_id,value.max_cost,now(),tid))
                home=store.root/"task-inputs"
                home.mkdir(exist_ok=True)
                (home/f"{tid}.json").write_text(dump({"prompt":prompt,"context":context}),encoding="utf-8")
                store.event(db,pid,"task.started",{"task_id":tid,"session_id":session_id,"reserved":value.max_cost})
                launch=(pid,tid,session_id,prompt["text"]+presentation_rules(task['role'])+CAPABILITY_RULES,context)
                return store.task(db,tid)
            result=store.idempotent(db,pid,"task.run",x_request_id,{"tid":tid,**value.model_dump()},operation)
        if launch:
            executor.submit(model_worker,*launch)
        return result

    @app.post("/api/projects/{pid}/tasks/{tid}/reconcile")
    def reconcile(pid:str,tid:str,value:Reconcile,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            def operation():
                t=store.task(db,tid,pid)
                if not t["reservation"] or t["status"] in ("running","input_changed"):
                    raise DomainError("任务未产生待核账费用或仍在运行")
                db.execute("UPDATE tasks SET actual_cost=?,cost_evidence=?,updated_at=? WHERE id=?",(value.actual_cost,value.evidence,now(),tid))
                store.event(db,pid,"cost.reconciled",{"task_id":tid,"actual_cost":value.actual_cost,"evidence":value.evidence})
                return store.task(db,tid)
            return store.idempotent(db,pid,"cost.reconcile",x_request_id,{"tid":tid,**value.model_dump()},operation)

    @app.post("/api/projects/{pid}/tasks/{tid}/proposals/{index}")
    def import_proposal(pid:str,tid:str,index:int,x_request_id:str=Header(default="")):
        with store.transaction() as db:
            def operation():
                t=store.task(db,tid,pid)
                if t["status"]!="accepted":
                    raise DomainError("请先验收成果再导入建议")
                a=store.record(db,pid,"artifact",t["result_id"])
                store.validate_refs(db,pid,[ref(a)])
                suggestions=a["data"].get("proposed_records",[])
                if not 0<=index<len(suggestions):
                    raise DomainError("建议不存在",404)
                suggestion=suggestions[index]
                data={**suggestion["data"],"origin":"model_proposal"}
                if suggestion["kind"]=="hypothesis":
                    data.update(evidence_status="untested",source_type="model_inference",evidence="")
                validate_data(suggestion["kind"],data)
                return store.save_record(db,pid,suggestion["kind"],data,record_id=f"proposal_{tid}_{a['version']}_{index}",refs=[ref(a)])
            return store.idempotent(db,pid,"proposal.import",x_request_id,{"tid":tid,"index":index},operation)

    @app.post("/api/projects/{pid}/assets")
    async def upload(pid:str,file:UploadFile=File(...)):
        extension=Path(file.filename or "").suffix.lower()
        types={".png":"image",".jpg":"image",".jpeg":"image",".webp":"image",".mp4":"video",".mov":"video",".wav":"audio",".mp3":"audio"}
        if extension not in types:
            raise DomainError("支持 PNG/JPG/WebP/MP4/MOV/WAV/MP3",422)
        with store.connect() as db:
            store.project(db,pid)
        asset_id=uid("asset")
        folder=store.root/"media"/pid
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/f"{asset_id}{extension}"
        size=0
        try:
            with path.open("wb") as out:
                while block:=await file.read(1024*1024):
                    size+=len(block)
                    if size>100*1024*1024:
                        raise DomainError("单个素材最多100MB",413)
                    out.write(block)
            if not size:
                raise DomainError("素材为空",422)
            if types[extension]=="image":
                with Image.open(path) as im:
                    im.verify()
            else:
                import imageio_ffmpeg
                probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),"-v","error","-i",str(path),"-t","0.05","-f","null","-"],capture_output=True,timeout=20,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                if probe.returncode:
                    raise DomainError("素材无法解码，请检查视频或音频文件",422)
        except Exception as e:
            path.unlink(missing_ok=True)
            if isinstance(e,DomainError):
                raise
            raise DomainError("素材格式无法验证",422)
        with store.transaction() as db:
            return store.save_record(db,pid,"asset",{"name":Path(file.filename or "素材").name,"path":str(path),"url":f"/media/{pid}/{path.name}","media_type":types[extension],"size":size,"license":"待核实","source":"human_upload"},asset_id)

    @app.post("/api/projects/{pid}/assets/{aid}/replace-image")
    async def replace_image(pid:str,aid:str,file:UploadFile=File(...),expected_version:int=Form(...,ge=1)):
        extension=Path(file.filename or "").suffix.lower()
        if extension not in (".png",".jpg",".jpeg",".webp"):
            raise DomainError("替换图片支持 PNG/JPG/WebP",422)
        with store.connect() as db:
            current=store.record(db,pid,"asset",aid)
            if current["data"].get("media_type")!="image":
                raise DomainError("只能替换图片素材",422)
            if current["version"]!=expected_version:
                raise DomainError("图片已被修改，请刷新后重新选择")
        folder=store.root/"media"/pid
        folder.mkdir(parents=True,exist_ok=True)
        # Each version owns its file: old URLs remain usable and caches cannot show the old image.
        path=folder/f"{aid}-{uid('upload')}{extension}"
        size=0
        try:
            with path.open("wb") as out:
                while block:=await file.read(1024*1024):
                    size+=len(block)
                    if size>10*1024*1024:
                        raise DomainError("替换图片最多10MB",413)
                    out.write(block)
            if not size:
                raise DomainError("图片为空",422)
            try:
                with Image.open(path) as im:
                    if im.format not in ("PNG","JPEG","WEBP"):
                        raise DomainError("图片实际格式须为 PNG/JPG/WebP",422)
                    if im.format!={".png":"PNG",".jpg":"JPEG",".jpeg":"JPEG",".webp":"WEBP"}[extension]:
                        raise DomainError("图片扩展名与实际格式不符，请更正文件扩展名",422)
                    width,height=im.size
                    if width*height>Image.MAX_IMAGE_PIXELS:
                        raise DomainError("图片像素尺寸过大",422)
                    im.verify()
                with Image.open(path) as im:
                    im.load()
            except (Image.DecompressionBombError,Image.DecompressionBombWarning):
                raise DomainError("图片像素尺寸过大",422)
            except DomainError:
                raise
            except Exception:
                raise DomainError("图片无法读取，请选择有效的 PNG/JPG/WebP 文件",422)
            with store.transaction() as db:
                current=store.record(db,pid,"asset",aid)
                # Draft discussions can adopt a user's new choice. Accepted work and
                # already generated dependent media must still go through review.
                drafts=[t for t in store.tasks(db,pid) if t["role"]=="storyboard"
                        and not t.get("superseded_by") and not t.get("merged_into")
                        and t["status"] in ("draft","discussing","pending_review","changes_requested")
                        and any(q["kind"]=="asset" and q["id"]==aid for q in t["input_refs"])]
                data={"name":current["data"].get("name") or Path(file.filename or "图片").name,
                      "path":str(path),"url":f"/media/{pid}/{path.name}","media_type":"image",
                      "size":size,"width":width,"height":height,"license":"待核实",
                      "source":"human_replacement","uploaded_filename":Path(file.filename or "图片").name,
                      "replaces_version":expected_version,"human_accepted":False}
                # The uploaded pixels are independent of the original model inputs.
                # Keep dependent outputs stale; never claim they were regenerated or re-approved.
                result=store.save_record(db,pid,"asset",data,aid,expected_version)
                for task in drafts:
                    refs=[];omitted=[]
                    for q in task["input_refs"]:
                        if q["kind"] in ("asset","shot"):
                            latest=store.record(db,pid,q["kind"],q["id"])
                            if latest["stale"]:
                                omitted.append(q)
                                continue
                            q=ref(latest)
                        if q not in refs: refs.append(q)
                    try:
                        store.dependencies_ready(db,{**task,"input_refs":refs})
                    except DomainError:
                        continue
                    db.execute("UPDATE tasks SET status=?,input_refs=?,updated_at=? WHERE id=?",
                               ("draft" if task["status"]=="draft" else "discussing",dump(refs),now(),task["id"]))
                    message=f"【工作台记录】用户上传图片替换了素材 {aid}，当前版本 V{result['version']}。请以新图片为后续参考，旧图的画面描述和质量判断不再沿用；已生成的关联结果需要复查，上传不等于验收。"
                    if omitted:
                        message+="已过期的素材或镜头暂未纳入当前有效输入："+dump(omitted)
                    db.execute("INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)",
                               (uid("message"),pid,task["id"],None,"user",message,None,1,now()))
                store.event(db,pid,"asset.image_replaced",{"asset_id":aid,"version":result["version"],"previous_version":expected_version})
                return result
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.put("/api/projects/{pid}/assets/{aid}")
    def edit_asset(pid:str,aid:str,value:AssetEdit):
        with store.transaction() as db:
            current=store.record(db,pid,"asset",aid)
            return store.save_record(db,pid,"asset",{**current["data"],**value.model_dump(exclude={"expected_version"})},aid,value.expected_version,current["input_refs"])

    @app.get("/api/projects/{pid}/records/{kind}/{rid}/history")
    def history(pid:str,kind:str,rid:str):
        with store.connect() as db:
            store.project(db,pid)
            return [store.record_dict(r) for r in db.execute("SELECT * FROM records WHERE project_id=? AND kind=? AND id=? ORDER BY version DESC",(pid,kind,rid))]

    def export_worker(pid,eid,project,shots,assets,audio_options):
        try:
            output=render_animatic(store.root/"media"/pid/eid,project,shots,assets,audio_options=audio_options)
            public={**output,"video_url":f"/media/{pid}/{eid}/animatic.mp4","manifest_url":f"/media/{pid}/{eid}/manifest.json","subtitles_url":f"/media/{pid}/{eid}/subtitles.srt","final_movie":False}
            with store.transaction() as db:
                old=json.loads(db.execute("SELECT data FROM exports WHERE id=?",(eid,)).fetchone()[0])
                db.execute("UPDATE exports SET status='completed',data=? WHERE id=?",(dump({**old,**public}),eid))
                store.event(db,pid,"animatic.exported",{"export_id":eid,"duration":output["duration"],"final_movie":False})
        except Exception as e:
            message=e.message if isinstance(e,DomainError) else f"{type(e).__name__}：渲染失败"
            with store.transaction() as db:
                db.execute("UPDATE exports SET status='failed',error=? WHERE id=?",(message,eid))
                store.event(db,pid,"animatic.failed",{"export_id":eid,"message":message})

    @app.post("/api/projects/{pid}/exports")
    def export(pid:str,value:ExportInput,x_request_id:str=Header(default="")):
        launch=None
        with store.transaction() as db:
            def operation():
                nonlocal launch
                project=store.project(db,pid)
                shots=[store.record(db,pid,"shot",rid) for rid in value.shot_ids]
                refs=[ref(s) for s in shots]
                assets={}
                for s in shots:
                    if s["data"].get("asset_id"):
                        a=store.record(db,pid,"asset",s["data"]["asset_id"])
                        refs.append(ref(a))
                        assets[a["id"]]=a["data"]
                if value.narration_asset_id:
                    a=store.record(db,pid,"asset",value.narration_asset_id)
                    if a["data"].get("media_type") != "audio":
                        raise DomainError("旁白须为本项目已导入的音频素材",422)
                    refs.append(ref(a))
                    assets[a["id"]]=a["data"]
                store.validate_refs(db,pid,refs)
                for asset in assets.values():
                    path=Path(asset.get("path", "")).resolve()
                    if not path.is_relative_to((store.root/"media"/pid).resolve()) or not path.is_file():
                        raise DomainError("导出素材路径无效",422)
                if sum(float(s["data"].get("duration",0)) for s in shots)>300:
                    raise DomainError("动态分镜总时长最多300秒",422)
                eid=uid("export")
                audio_options=value.model_dump(exclude={"shot_ids","name"})
                if value.narration_asset_id and value.narration_start_seconds >= sum(float(s["data"].get("duration",0)) for s in shots):
                    raise DomainError("旁白开始位置须小于导出总时长",422)
                db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?)",(eid,pid,"running",dump(refs),dump({"name":value.name,"brief_revision":project["revision"],"audio_options":audio_options}),None,now()))
                store.event(db,pid,"animatic.started",{"export_id":eid,"shots":len(shots)})
                launch=(pid,eid,project,shots,assets,audio_options)
                return {"id":eid,"status":"running"}
            result=store.idempotent(db,pid,"export",x_request_id,value.model_dump(),operation)
        if launch:
            executor.submit(export_worker,*launch)
        return result

    register_chat(app,store,runtime,executor,ready,submit,balance,review_task,validate_stage)

    media=store.root/"media"
    media.mkdir(exist_ok=True)
    app.mount("/media",StaticFiles(directory=media),name="media")
    dist=ROOT/"frontend"/"dist"
    if (dist/"assets").exists():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="static")

    @app.get("/{path:path}")
    def index(path:str):
        if path.startswith("api/"):
            raise DomainError("接口不存在",404)
        if not (dist/"index.html").exists():
            return JSONResponse({"detail":"请先运行 npm run build --prefix frontend"},503)
        return FileResponse(dist/"index.html")

    return app

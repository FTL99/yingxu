"""Isolated UI QA for returning to a role and synchronizing changes. No external API."""
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.models import RoleResult


class Runner:
    def status(self): return {"ready":True}
    def run(self,*args):
        role=args[-1]["task"]["role"]
        return RoleResult(title="新版角色方案 · 隔离模拟测试",summary="主角改为女船长，保留海盗主线、60秒节奏和原有镜头素材。",
            body=f"已按你的反馈调整{role}阶段方案：主角改为**女船长**，高潮由她指挥反攻。\n\n保留海盗主线、60秒节奏和已有素材。其他角色需要检查称呼、人物描述、服装与镜头的对应关系。\n\n你确认后可以点击「确认修改并同步其他 Agent」。\n\n这是本地模拟回复，没有调用付费接口。",
            uncertainties=["真实观众反应仍未验证"])


root=Path(__file__).resolve().parents[1]/"data"/("qa-role-revisions-"+uuid.uuid4().hex[:8])
app=create_app(root,Runner())


@app.middleware("http")
async def qa_origin(request,call_next):
    if request.headers.get("origin")=="http://127.0.0.1:8812":
        request.scope["headers"]=[(name,b"http://127.0.0.1:8811" if name==b"origin" else value) for name,value in request.scope["headers"]]
    return await call_next(request)


c=TestClient(app)
c.headers["X-Studio-Token"]=c.get("/api/health").json()["token"]
def post(path,data):
    r=c.post("/api"+path,json=data,headers={"X-Request-ID":uuid.uuid4().hex})
    assert r.status_code==200,r.text
    return r.json()


pid=post("/projects",{"name":"角色修改与同步 · 隔离模拟测试","audience":"喜欢情绪反转的观众","promise":"用一个海盗故事表达成长","followup":"后续同主题故事","duration":60,"budget":100})["id"]
post(f"/projects/{pid}/records/hypothesis",{"data":{"claim":"观众会理解主角的成长","owner":"director","source_type":"user_intuition","evidence_status":"untested"}})
tasks=post(f"/projects/{pid}/pipeline",{})
for t in tasks:
    if t["role"]=="post": continue
    artifact=post(f"/projects/{pid}/tasks/{t['id']}/submit",{"title":t['role']+"原版方案","summary":"男船长，海盗主线，60秒成长故事。",
        "body":"原版设定：主角是男船长，在海盗船上从犹豫走向主动反攻。\n\n| 场景 | 内容 | 时长 |\n| --- | --- | --- |\n| 开场 | 船舱里的枪响 | 3秒 |\n| 困境 | 船长被围困 | 15秒 |\n| 高潮 | 船长指挥反攻 | 30秒 |\n| 收尾 | 留下新的选择 | 12秒 |",
        "uncertainties":["真实观众反应未验证"]})
    q={"kind":"artifact","id":artifact["id"],"version":artifact["version"]}
    post(f"/projects/{pid}/tasks/{t['id']}/review",{"outcome":"accepted","acknowledged_uncertainties":True,"checks":[{"criterion":"已确认文字方案","status":"passed","note":"QA mock human confirmation","evidence_refs":[q]}]})
    stage={"audience":"G0","writer":"G1","storyboard":"G2"}.get(t['role'])
    if stage:
        post(f"/projects/{pid}/records/decision",{"data":{"stage":stage,"allow":True,"risks_acknowledged":True,"note":"QA mock phase approval"},"input_refs":[q]})
c.close()

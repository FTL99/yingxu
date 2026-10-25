"""Isolated browser QA only: no credentials, network model, or production data."""
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.models import RoleResult


class MockRunner:
    def status(self): return {'ready': True}
    def run(self, *args):
        context=args[-1]
        names={'director':'总控','writer':'编剧','assets':'资产','storyboard':'分镜','post':'后期','audience':'观众策略'}
        role=context['task']['role']
        ancestors=context.get('handoff_package',{}).get('upstream',[])
        received='、'.join(names[t['role']] for t in ancestors) or 'Brief'
        return RoleResult(title=names[role]+'交接测试',summary='模拟回复，不是真实模型',
            body=f'已收到：{received}。\n\n继承已确认的海盗主线、60秒规格，以及先实测两个最贵镜头再批量生成的约束。\n\n'+
                 ('请确认是否交给编剧。' if role=='director' else '开始本阶段工作：先写开场与情绪转折。'),
            uncertainties=['真实生成成本未验证'],proposed_records=[{'kind':'hypothesis','data':{'claim':'预算能承载已选方向'}}])


root=Path(__file__).resolve().parents[1]/'data'/('qa-advance-'+uuid.uuid4().hex[:8])
app=create_app(root,MockRunner())

@app.middleware('http')
async def qa_origin(request,call_next):
    # This isolated QA app uses port 8812; production's origin policy remains unchanged.
    if request.headers.get('origin')=='http://127.0.0.1:8812':
        request.scope['headers']=[(name,b'http://127.0.0.1:8811' if name==b'origin' else value)
                                  for name,value in request.scope['headers']]
    return await call_next(request)

client=TestClient(app)  # No lifespan shutdown; Uvicorn owns the application's lifespan.
client.headers['X-Studio-Token']=client.get('/api/health').json()['token']
pid=client.post('/api/projects',json={'name':'自动交接测试（模拟）','audience':'测试观众','promise':'测试表达','followup':'测试后续','budget':20,'duration':60,'aspect':'16:9'},headers={'X-Request-ID':'qa-project'}).json()['id']
tasks=client.post(f'/api/projects/{pid}/pipeline',headers={'X-Request-ID':'qa-pipeline'}).json()
tid=tasks[0]['id']
client.post(f'/api/projects/{pid}/tasks/{tid}/chat',json={'message':'选海盗主线，先实测最贵镜头。','max_cost':1},headers={'X-Request-ID':'qa-initial'})
for _ in range(200):
    state=client.get(f'/api/projects/{pid}/tasks/{tid}/chat').json()
    if not state['running']: break
    time.sleep(.01)
client.close()

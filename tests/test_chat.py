import threading
import time

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.models import RoleResult
from studio.store import Store, DomainError


class Runner:
    def __init__(self):
        self.calls=[]
        self.release=threading.Event()
        self.release.set()
        self.fail=False
    def status(self): return {'ready':True}
    def run(self,*args):
        self.calls.append(args)
        self.release.wait(3)
        if self.fail: raise DomainError('测试中断，不自动重試')
        return RoleResult(title='讨论中的决定',summary='保留海盗方向',body='先把开场改成自嘲。\n\n| 方向 | 判断 |\n|---|---|\n| 海盗 | 保留 |',uncertainties=['真实观众反馈未验证'])


def setup(tmp_path):
    runner=Runner();app=create_app(tmp_path,runner)
    client=TestClient(app)
    client.headers['X-Studio-Token']=client.get('/api/health').json()['token']
    post(client,'/projects',{'name':'对话片','budget':20},'project')
    pid=client.get('/api/projects').json()[0]['id']
    tasks=post(client,f'/projects/{pid}/pipeline',{},'pipeline')
    return runner,client,pid,tasks


def post(c,path,data,rid='once',status=200):
    r=c.post('/api'+path,json=data,headers={'X-Request-ID':rid})
    assert r.status_code==status,r.text
    return r.json()


def done(c,path):
    for _ in range(100):
        v=c.get('/api'+path).json()
        if not v['running']: return v
        time.sleep(.01)
    assert False,'worker did not finish'


def test_multiple_turns_history_cost_and_explicit_finalize(tmp_path):
    runner,c,pid,ts=setup(tmp_path)
    tid=ts[0]['id'];base=f'/projects/{pid}/tasks/{tid}';chat=base+'/chat'
    first=post(c,chat,{'message':'选择海盗方向，但不要说教','max_cost':1},'turn-one')
    assert post(c,chat,{'message':'选择海盗方向，但不要说教','max_cost':1},'turn-one')==first
    view=done(c,chat)
    assert len(runner.calls)==1 and len(view['messages'])==2
    assert view['task']['status']=='discussing' and view['task']['result_id'] is None
    post(c,f"/projects/{pid}/tasks/{ts[1]['id']}/chat",{'message':'继续','max_cost':1},'blocked',409)
    post(c,chat,{'message':'把我们的决定整理成交接要点','max_cost':2,'mode':'finalize'},'turn-two')
    view=done(c,chat)
    context=runner.calls[-1][-1]
    assert context['mode']=='finalize' and context['project']['budget_currency']=='CNY'
    assert [r['role'] for r in context['conversation']]==['user','assistant','user']
    assert '不要说教' in context['conversation'][0]['content']
    assert c.get('/api/projects/'+pid).json()['balance']['reserved']==3
    post(c,chat+'/finalize',{'message_id':view['messages'][1]['id']},'outdated',409)
    artifact=post(c,chat+'/finalize',{'message_id':view['messages'][-1]['id']},'freeze')
    assert post(c,chat+'/finalize',{'message_id':view['messages'][-1]['id']},'freeze')==artifact
    assert artifact['data']['source']=='role_chat'
    assert c.get('/api'+chat).json()['task']['status']=='pending_review'
    # Saving a result is not approval, and never invokes another model.
    assert len(runner.calls)==2
    post(c,f"/projects/{pid}/tasks/{ts[1]['id']}/chat",{'message':'下游','max_cost':1},'still-blocked',409)
    post(c,f"/projects/{pid}/chat/{first['id']}/reconcile",{'actual_cost':.1,'evidence':'本地测试账单'},'bill')
    balance=c.get('/api/projects/'+pid).json()['balance']
    assert balance['reserved']==2 and balance['actual']==.1
    approved=post(c,base+'/review',{'outcome':'accepted','acknowledged_uncertainties':True,'checks':[{'criterion':'符合已确认决定','status':'passed','note':'查看开场及交接要点','evidence_refs':[{'kind':'artifact','id':artifact['id'],'version':artifact['version']}]}]},'review')
    post(c,chat,{'message':'已验收阶段直接改动','max_cost':1},'locked',409)
    post(c,f"/projects/{pid}/tasks/{ts[1]['id']}/chat",{'message':'基于导演的已确认决定继续','max_cost':1},'handoff')
    done(c,f"/projects/{pid}/tasks/{ts[1]['id']}/chat")
    assert runner.calls[-1][-1]['capabilities']['audience_video_validation']['enabled'] is False
    assert runner.calls[-1][-1]['capabilities']['audience_mode']=='strategy_only'
    assert '观众视频校验已禁用' in runner.calls[-1][-2]
    assert runner.calls[-1][-1]['tools']==[]
    assert any(r['id']==artifact['id'] and r['version']==artifact['version'] for r in runner.calls[-1][-1]['inputs'])
    # Frozen history survives process reconstruction.
    c.close()
    app=create_app(tmp_path,Runner())
    with TestClient(app) as restored:
        v=restored.get('/api'+chat).json()
        assert len(v['messages'])==4 and v['task']['status']=='accepted'


def test_existing_artifact_seed_and_busy_gate(tmp_path):
    runner,c,pid,ts=setup(tmp_path);tid=ts[0]['id'];base=f'/projects/{pid}/tasks/{tid}';chat=base+'/chat'
    old=post(c,base+'/submit',{'title':'原回答','summary':'摘要','body':'旧方向与表格'},'old')
    runner.release.clear()
    sent=post(c,chat,{'message':'继续讨论旧方向','max_cost':1},'start')
    post(c,chat,{'message':'第二次','max_cost':1},'concurrent',409)
    post(c,base+'/review',{'outcome':'accepted','checks':[{'criterion':'目标','status':'passed','note':'已看','evidence_refs':[{'kind':'artifact','id':old['id'],'version':old['version']}]}]},'no-stale-review',409)
    runner.release.set();view=done(c,chat)
    assert view['messages'][0]['text']=='旧方向与表格'
    assert len(runner.calls)==1 and runner.calls[0][-1]['conversation'][0]['content']=='旧方向与表格'
    assert c.get('/api/projects/'+pid).json()['balance']['reserved']==1
    c.close()


def test_failed_chat_keeps_fee_and_never_finalizes(tmp_path):
    runner,c,pid,ts=setup(tmp_path);runner.fail=True;tid=ts[0]['id'];chat=f'/projects/{pid}/tasks/{tid}/chat'
    post(c,chat,{'message':'请继续','max_cost':2},'failed')
    view=done(c,chat)
    assert len(runner.calls)==1 and view['task']['status']=='interrupted'
    assert view['jobs'][0]['actual_cost'] is None and view['jobs'][0]['reservation']==2
    post(c,chat+'/finalize',{'message_id':view['messages'][-1]['id']},'no-freeze',409)
    other=post(c,'/projects',{'name':'另一个'},'other')['id']
    assert c.get(f'/api/projects/{other}/tasks/{tid}/chat').status_code==404
    c.close()


def test_chat_input_change_cannot_be_approved(tmp_path):
    runner,c,pid,ts=setup(tmp_path);runner.release.clear();tid=ts[0]['id'];chat=f'/projects/{pid}/tasks/{tid}/chat'
    post(c,chat,{'message':'讨论','max_cost':1},'start')
    current=c.get('/api/projects/'+pid).json()['project']
    edit={k:v for k,v in current.items() if k not in ('id','revision','created_at')}
    edit.update(name='改动的Brief',expected_revision=current['revision'])
    assert c.put('/api/projects/'+pid,json=edit).status_code==200
    runner.release.set();view=done(c,chat)
    assert view['jobs'][0]['status']=='interrupted' and view['task']['status']=='needs_review'
    post(c,chat+'/finalize',{'message_id':view['messages'][-1]['id']},'stale',409)
    c.close()

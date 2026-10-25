import io
import json
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.app import create_app, ref
from studio.models import RoleResult
from studio.runtime import HarnessRuntime
from studio.store import Store, DomainError
from studio.media import render_animatic
from studio.prompts import role_prompt


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app) as c:
        c.headers.update({'X-Studio-Token':c.get('/api/health').json()['token']})
        yield c


def post(c,path,data,request_id=None,status=200):
    r=c.post('/api'+path,json=data,headers={'X-Request-ID':request_id or uuid.uuid4().hex})
    assert r.status_code==status,r.text
    return r.json()


def project(c,budget=10):
    return post(c,'/projects',{'name':'测试项目','audience':'独居观众','promise':'被看见','followup':'同主题后续故事','budget':budget})['id']


def record(c,pid,kind,data,refs=None,**extras):
    return post(c,f'/projects/{pid}/records/{kind}',{'data':data,'input_refs':refs or [],**extras})


def hypothesis(c,pid):
    return record(c,pid,'hypothesis',{'claim':'观众理解故事','owner':'writer','source_type':'user_intuition','evidence_status':'untested'})


def submit(c,pid,tid,**extra):
    return post(c,f'/projects/{pid}/tasks/{tid}/submit',{'title':'真实提交的成果','summary':'摘要','body':'测试正文','uncertainties':[],'proposed_records':[],**extra})


def accept(c,pid,tid,artifact,criteria=None):
    return post(c,f'/projects/{pid}/tasks/{tid}/review',{'outcome':'accepted','checks':[{'criterion':s,'status':'passed','note':'人工查看具体正文','evidence_refs':[ref(artifact)]} for s in (criteria or ['目标与约束'])]})


def gate(c,pid,stage,a,**extra):
    return record(c,pid,'decision',{'stage':stage,'allow':True,'risks_acknowledged':True,'note':'允许继续，并保留未验证风险'},[ref(a)],**extra)


def chain(c,pid):
    hypothesis(c,pid)
    ts={t['role']:t for t in post(c,f'/projects/{pid}/pipeline',{})}
    outputs={}
    gates={}
    for role in ('director','audience','writer','assets','storyboard','post'):
        a=submit(c,pid,ts[role]['id'])
        accept(c,pid,ts[role]['id'],a)
        outputs[role]=a
        stage={'audience':'G0','writer':'G1','storyboard':'G2'}.get(role)
        if stage:
            gates[stage]=gate(c,pid,stage,a)
    return ts,outputs,gates


def test_project_create_idempotency(client):
    value={'name':'幂等项目'}
    a=post(client,'/projects',value,'same')
    b=post(client,'/projects',value,'same')
    assert a==b
    assert len(client.get('/api/projects').json())==1
    post(client,'/projects',{'name':'不同输入'},'same',409)


def test_real_evidence_required(client):
    pid=project(client)
    data={'claim':'会关注','owner':'audience','source_type':'model_inference','evidence_status':'supported','evidence':'模拟观众说喜欢'}
    post(client,f'/projects/{pid}/records/hypothesis',{'data':data},status=422)
    data['source_type']='invented_source'
    post(client,f'/projects/{pid}/records/hypothesis',{'data':data},status=422)
    data.update(source_type='behavior_data',evidence='外部真实测试日志位置')
    assert record(client,pid,'hypothesis',data)['data']['evidence_status']=='supported'


def test_acceptance_is_separate_and_version_exact(client):
    pid=project(client)
    tasks=post(client,f'/projects/{pid}/pipeline',{})
    first=tasks[0];second=tasks[1]
    a=submit(client,pid,first['id'])
    post(client,f"/projects/{pid}/tasks/{second['id']}/submit",{'title':'结果','summary':'摘要','body':'正文'},status=409)
    for state in ('unknown','failed'):
        post(client,f"/projects/{pid}/tasks/{first['id']}/review",{'outcome':'accepted','checks':[{'criterion':'目标','status':state,'evidence_refs':[ref(a)]}]},status=422)
    wrong={**ref(a),'version':99}
    post(client,f"/projects/{pid}/tasks/{first['id']}/review",{'outcome':'accepted','checks':[{'criterion':'目标','status':'passed','evidence_refs':[wrong],'note':'证据'}]},status=409)
    accept(client,pid,first['id'],a)
    submit(client,pid,second['id'])


def test_phase_gate_and_revocation_invalidates_downstream(client):
    pid=project(client)
    ts,outputs,gates=chain(client,pid)
    r=client.get('/api/projects/'+pid).json()
    assert all(t['status']=='accepted' for t in r['tasks'])
    old=gates['G0']
    record(client,pid,'decision',{'stage':'G0','allow':False,'note':'新证据出现，暂停'},[ref(outputs['audience'])],id=old['id'],expected_version=old['version'])
    r=client.get('/api/projects/'+pid).json()
    changed={t['role']:t for t in r['tasks']}
    assert changed['director']['status']=='accepted'
    assert changed['audience']['status']=='accepted'
    assert changed['writer']['status']=='needs_review'
    assert changed['post']['status']=='needs_review'
    assert next(x for x in r['records'] if x['id']==gates['G1']['id'])['stale']


def test_optimistic_versioning_and_cycle_detection(tmp_path):
    store=Store(tmp_path)
    with store.transaction() as db:
        db.execute("INSERT INTO projects VALUES('p',1,'{}','now')")
        a=store.save_record(db,'p','reference',{'source':'local'})
        b=store.save_record(db,'p','reference',{'source':'other'},refs=[ref(a)])
        with pytest.raises(DomainError,match='循环'):
            store.save_record(db,'p','reference',{'source':'changed'},a['id'],1,[ref(b)])
        with pytest.raises(DomainError,match='修改'):
            store.save_record(db,'p','reference',{},a['id'],0)
        unrelated=store.save_record(db,'p','reference',{'source':'third'})
        store.save_record(db,'p','reference',{'source':'new'},a['id'],1)
        assert store.record(db,'p','reference',b['id'])['stale']
        assert not store.record(db,'p','reference',unrelated['id'])['stale']


def test_brief_revision_invalidates_shots_depending_on_artifacts(client):
    pid=project(client)
    task=post(client,f'/projects/{pid}/tasks',{'role':'director','goal':'计划'})
    a=submit(client,pid,task['id'])
    shot=record(client,pid,'shot',{'duration':1,'description':'计划镜头'},[ref(a)])
    p=client.get('/api/projects/'+pid).json()['project']
    edit={k:v for k,v in p.items() if k not in ('id','revision','created_at')}
    result=client.put('/api/projects/'+pid,json={**edit,'name':'修改目标','expected_revision':1})
    assert result.status_code==200
    r=client.get('/api/projects/'+pid).json()
    assert next(x for x in r['records'] if x['id']==shot['id'])['stale']


def test_no_paid_call_without_configuration(client):
    pid=project(client)
    t=post(client,f'/projects/{pid}/tasks',{'role':'director','goal':'计划'})
    post(client,f"/projects/{pid}/tasks/{t['id']}/run",{'max_cost':1},status=422)
    assert client.get('/api/projects/'+pid).json()['balance']['reserved']==0


class ControlledRunner:
    def __init__(self):
        self.calls=0
        self.entered=threading.Event()
        self.release=threading.Event()
        self.context=None
    def status(self):
        return {'ready':True}
    def run(self,*args):
        self.calls+=1
        self.context=args[-1]
        self.entered.set()
        self.release.wait(3)
        return RoleResult(title='SDK合同测试',summary='测试替身',body='测试替身结果；非真实模型调用')


def test_paid_intent_idempotency_budget_and_reconciliation(tmp_path):
    runner=ControlledRunner()
    app=create_app(tmp_path,runner)
    with TestClient(app) as c:
        c.headers['X-Studio-Token']=c.get('/api/health').json()['token']
        pid=project(c,budget=2)
        t=post(c,f'/projects/{pid}/tasks',{'role':'director','goal':'计划'})
        second=post(c,f'/projects/{pid}/tasks',{'role':'director','goal':'另一个计划'})
        a=post(c,f"/projects/{pid}/tasks/{t['id']}/run",{'max_cost':2},'owned-call')
        assert runner.entered.wait(1)
        assert runner.context['project']['budget_currency']=='CNY'
        assert runner.context['capabilities']['audience_video_validation']['enabled'] is False
        assert len(runner.context['workflow_snapshot'])==2
        assert set(runner.context['phase_contract'])=={'G0','G1','G2','G3'}
        b=post(c,f"/projects/{pid}/tasks/{t['id']}/run",{'max_cost':2},'owned-call')
        assert a==b and runner.calls==1
        post(c,f"/projects/{pid}/tasks/{second['id']}/run",{'max_cost':1},status=409)
        post(c,f"/projects/{pid}/tasks/{t['id']}/reconcile",{'actual_cost':0,'evidence':'未知'},status=409)
        runner.release.set()
        for _ in range(40):
            snap=c.get('/api/projects/'+pid).json()
            if snap['tasks'][0]['status']!='running':break
            time.sleep(.02)
        assert snap['tasks'][0]['status']=='pending_review'
        assert snap['balance']['reserved']==2
        post(c,f"/projects/{pid}/tasks/{t['id']}/reconcile",{'actual_cost':.8,'evidence':'供应商测试账单位置'})
        snap=c.get('/api/projects/'+pid).json()
        assert snap['balance']['reserved']==0 and snap['balance']['actual']==.8


def test_audience_video_validation_is_disabled_without_disabling_strategy(client):
    pid=project(client)
    policy=client.get('/api/capabilities').json()
    assert policy['audience_mode']=='strategy_only'
    assert policy['audience_video_validation']['enabled'] is False
    assert client.get('/api/projects/'+pid).json()['capabilities']==policy
    # Keeping the role allows text strategy and ordinary human review to continue.
    task=post(client,f'/projects/{pid}/tasks',{'role':'audience','goal':'只讨论受众与创意'})
    assert task['status']=='draft'
    artifact=post(client,f"/projects/{pid}/tasks/{task['id']}/submit",{'title':'策略草案','summary':'未验证','body':'情绪与关注动机仍需真实反馈'})
    assert artifact['data']['body']=='情绪与关注动机仍需真实反馈'


def test_crash_recovery_keeps_unknown_charge(tmp_path):
    store=Store(tmp_path)
    with store.transaction() as db:
        db.execute("INSERT INTO projects VALUES('p',1,'{}','now')")
        db.execute("INSERT INTO tasks(id,project_id,dependencies,input_refs,status,reservation,created_at) VALUES('t','p','[]','[]','running',2,'now')")
    store.recover()
    with store.connect() as db:
        t=store.task(db,'t')
        assert t['status']=='interrupted' and t['reservation']==2 and t['actual_cost'] is None


def test_upload_validation_and_metadata_version(client):
    pid=project(client)
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('../../x.svg',b'<script/>','image/svg+xml')})
    assert r.status_code==422
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('bad.png',b'not an image','image/png')})
    assert r.status_code==422
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('bad.mp4',b'not a video','video/mp4')})
    assert r.status_code==422
    im=Image.new('RGB',(16,16));buffer=io.BytesIO();im.save(buffer,'PNG')
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('../../x.png',buffer.getvalue(),'image/png')})
    assert r.status_code==200,r.text
    a=r.json()
    assert Path(a['data']['path']).name.startswith('asset_')
    r=client.put(f"/api/projects/{pid}/assets/{a['id']}",json={'expected_version':1,'name':'角色参考','license':'测试素材自制','source_note':'Pillow测试图片'})
    assert r.status_code==200 and r.json()['version']==2
    r=client.put(f"/api/projects/{pid}/assets/{a['id']}",json={'expected_version':2,'name':'攻击','license':'x','path':'C:/other'})
    assert r.status_code==422


def test_cross_project_references_and_csrf_are_rejected(client):
    p1=project(client);p2=project(client)
    h=hypothesis(client,p1)
    post(client,f'/projects/{p2}/records/shot',{'data':{'duration':1,'description':'x'},'input_refs':[ref(h)]},status=404)
    r=client.post('/api/projects',json={'name':'evil'},headers={'Origin':'https://evil.example'})
    assert r.status_code==403
    r=client.post('/api/projects',json={'name':'evil'},headers={'X-Studio-Token':'wrong'})
    assert r.status_code==403
    assert client.get('/api/health',headers={'Host':'evil.example'}).status_code==403


def test_animatic_real_container_and_subtitles(tmp_path):
    shots=[{'id':'s1','version':1,'data':{'duration':.5,'description':'初始画面','subtitle':'一句字幕'}}, {'id':'s2','version':1,'data':{'duration':.5,'description':'结束画面'}}]
    result=render_animatic(tmp_path/'export',{'aspect':'9:16'},shots,{},size=(144,256))
    assert Path(result['video']).stat().st_size>1000
    assert result['duration']==1
    assert '一句字幕' in Path(result['subtitles']).read_text(encoding='utf-8')
    manifest=json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
    assert not manifest['final_movie'] and manifest['shots'][0]['source']=='planning_card'


def test_final_movie_requires_real_asset_and_five_checks(client,tmp_path):
    pid=project(client)
    ts,outputs,gates=chain(client,pid)
    post(client,f'/projects/{pid}/records/decision',{'data':{'stage':'G3','allow':True,'risks_acknowledged':True,'note':'希望放行'},'input_refs':[ref(outputs['post'])]},status=422)
    video=render_animatic(tmp_path/'final',{'aspect':'9:16'},[{'id':'test','version':1,'data':{'duration':.3,'description':'测试画面'}}],{},size=(144,256))
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('actual.mp4',Path(video['video']).read_bytes(),'video/mp4')})
    assert r.status_code==200,r.text
    a=submit(client,pid,ts['post']['id'],expected_version=1,final_asset_id=r.json()['id'])
    post(client,f"/projects/{pid}/tasks/{ts['post']['id']}/review",{'outcome':'accepted','checks':[{'criterion':'泛泛检查','status':'passed','note':'x','evidence_refs':[ref(a)]}]},status=422)
    accept(client,pid,ts['post']['id'],a,['画面与一致性','声音与节奏','字幕与信息','时长与画幅','目标行为与后续价值'])
    final=gate(client,pid,'G3',a)
    assert final['data']['allow']


def test_prompts_are_six_element_compiled():
    for role in ('director','audience','writer','assets','storyboard','post'):
        p=role_prompt(role)
        assert p['text'].count('\n## ')==6
        assert '假设驱动补充' in p['text'] and 'JSON Schema' in p['text']
        assert all(f'（{label}）' in p['text'] for label in ['Goal','Role','User','Info','Rules','Format'])


def test_settings_never_return_key(client):
    r=client.put('/api/runtime',json={'enabled':False,'api_key':'placeholder-test-only','max_output_tokens':32768,'reasoning_effort':'high'})
    assert r.status_code==200
    assert 'placeholder-test-only' not in r.text and r.json()['has_key']
    r=client.put('/api/runtime',json={'enabled':False,'max_output_tokens':32768,'reasoning_effort':'high'})
    assert r.json()['has_key'] and r.json()['max_output_tokens']==32768 and r.json()['reasoning_effort']=='high'
    r=client.put('/api/runtime',json={'enabled':False,'clear_key':True})
    assert not r.json()['has_key']


def test_retry_preserves_cost_and_rewires_unused_drafts(tmp_path):
    runner=ControlledRunner()
    app=create_app(tmp_path,runner)
    with TestClient(app) as c:
        c.headers['X-Studio-Token']=c.get('/api/health').json()['token']
        pid=project(c)
        ts=post(c,f'/projects/{pid}/pipeline',{})
        old=ts[0];child=ts[1]
        store=Store(tmp_path)
        with store.transaction() as db:
            db.execute("UPDATE tasks SET status='interrupted',session_id='legacy-session',reservation=5,error='max-tokens' WHERE id=?",(old['id'],))
        replacement=post(c,f"/projects/{pid}/tasks/{old['id']}/retry",{},'prepare-once')
        assert post(c,f"/projects/{pid}/tasks/{old['id']}/retry",{},'prepare-once')==replacement
        assert runner.calls==0 and replacement['status']=='draft'
        snap=c.get('/api/projects/'+pid).json()
        previous=next(t for t in snap['tasks'] if t['id']==old['id'])
        assert previous['superseded_by']==replacement['id'] and previous['status']=='interrupted'
        assert previous['session_id']=='legacy-session' and previous['reservation']==5 and previous['actual_cost'] is None
        assert snap['balance']['reserved']==5
        assert next(t for t in snap['tasks'] if t['id']==child['id'])['dependencies']==[replacement['id']]
        post(c,f"/projects/{pid}/tasks/{old['id']}/run",{'max_cost':1},status=409)
        post(c,f"/projects/{pid}/tasks/{old['id']}/submit",{'title':'旧','summary':'摘要','body':'正文'},status=409)
        assert c.get(f"/api/projects/{project(c)}/tasks/{old['id']}/output").status_code==404
        assert runner.calls==0


def test_retry_rejects_downstream_with_execution_records(client,tmp_path):
    pid=project(client)
    ts=post(client,f'/projects/{pid}/pipeline',{})
    store=Store(tmp_path)
    with store.transaction() as db:
        db.execute("UPDATE tasks SET status='interrupted' WHERE id=?",(ts[0]['id'],))
        db.execute("UPDATE tasks SET session_id='already-dispatched' WHERE id=?",(ts[1]['id'],))
    post(client,f"/projects/{pid}/tasks/{ts[0]['id']}/retry",{},status=409)
    assert len(client.get('/api/projects/'+pid).json()['tasks'])==6

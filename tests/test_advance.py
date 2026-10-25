from fastapi.testclient import TestClient

from studio.app import create_app
from studio.models import RoleResult
from studio.store import Store
from tests.test_chat import post, done


class HandoffRunner:
    def __init__(self): self.calls=[]
    def status(self): return {'ready':True}
    def run(self,*args):
        self.calls.append(args)
        role=args[-1]['task']['role']
        return RoleResult(title=role+'阶段方案',summary='海盗主线，保留成本实测约束',
            body='海盗主线；2个最贵镜头实测后再批量生成。',uncertainties=['成本尚未验证'],
            proposed_records=[{'kind':'hypothesis','data':{'statement':'100元能够完成本方案','owner':'随意字段','status':'supported','evidence':'模型模拟'}}])


def setup(tmp_path,budget=20):
    runner=HandoffRunner();client=TestClient(create_app(tmp_path,runner))
    client.headers['X-Studio-Token']=client.get('/api/health').json()['token']
    pid=post(client,'/projects',{'name':'自动交接','audience':'守旧派','promise':'AI觉醒','followup':'个人页面','budget':budget},'p')['id']
    tasks={t['role']:t for t in post(client,f'/projects/{pid}/pipeline',{},'pipeline')}
    return runner,client,pid,tasks


def chatpath(pid,task): return f"/projects/{pid}/tasks/{task['id']}/chat"


def agree(client,pid,task,rid,merge=False,start=True,status=200,**changes):
    view=client.get('/api'+chatpath(pid,task)).json()
    data={'confirmed':True,'expected_brief_revision':1,'message_id':view['messages'][-1]['id'] if view['messages'] else None,
          'artifact_version':view['artifact_version'],'merge_audience':merge,'acknowledged_uncertainties':True,
          'start_next':start,'max_cost':1}
    data.update(changes)
    return post(client,f"/projects/{pid}/tasks/{task['id']}/advance",data,rid,status),data


def begin(client,pid,task):
    post(client,chatpath(pid,task),{'message':'选海盗，先做2个最贵镜头再批量生成','max_cost':1},'start')
    return done(client,chatpath(pid,task))


def test_director_merges_audience_and_full_chain_receives_decisions(tmp_path):
    runner,c,pid,ts=setup(tmp_path);begin(c,pid,ts['director'])
    result,data=agree(c,pid,ts['director'],'advance-director',merge=True)
    assert result['next_role']=='writer' and result['job_id']
    assert post(c,f"/projects/{pid}/tasks/{ts['director']['id']}/advance",data,'advance-director')==result
    done(c,chatpath(pid,ts['writer']))
    assert len(runner.calls)==2
    snap=c.get('/api/projects/'+pid).json()
    audience=next(t for t in snap['tasks'] if t['role']=='audience')
    assert audience['status']=='merged' and audience['result_id'] is None and audience['merged_into']==ts['director']['id']
    writer=next(t for t in snap['tasks'] if t['role']=='writer')
    assert writer['dependencies']==[ts['director']['id']]
    hypothesis=next(r for r in snap['records'] if r['kind']=='hypothesis')
    assert hypothesis['data']['source_type']=='model_inference' and hypothesis['data']['evidence_status']=='untested' and hypothesis['data']['evidence']==''
    first_handoff=runner.calls[-1][-1]['handoff_package']['upstream']
    assert [item['role'] for item in first_handoff]==['director']
    assert '最贵镜头' in first_handoff[0]['discussion'][0]['text']
    assert first_handoff[0]['artifact_ref']==result['artifact_ref']
    assert any(r['id']==result['artifact_ref']['id'] for r in runner.calls[-1][-1]['inputs'])
    for current,next_role in [('writer','assets'),('assets','storyboard'),('storyboard','post')]:
        outcome,_=agree(c,pid,ts[current],'advance-'+current)
        assert outcome['next_role']==next_role
        done(c,chatpath(pid,ts[next_role]))
        inherited=runner.calls[-1][-1]['handoff_package']['upstream']
        assert inherited[0]['role']=='director' and inherited[-1]['role']==current
        assert '最贵镜头' in inherited[0]['discussion'][0]['text']
        displayed=c.get(f"/api/projects/{pid}/tasks/{ts[next_role]['id']}/handoff").json()
        assert displayed['upstream']==inherited
    assert len(runner.calls)==5
    # Final-film acceptance remains real review, rather than another text click.
    agree(c,pid,ts['post'],'no-text-final',status=409)
    assert c.get('/api/projects/'+pid).json()['balance']['reserved']==5
    assert c.get('/api/gemini').json()['enabled'] is False
    c.close()


def test_six_roles_remain_available_and_writer_gets_director_ancestor(tmp_path):
    runner,c,pid,ts=setup(tmp_path);begin(c,pid,ts['director'])
    outcome,_=agree(c,pid,ts['director'],'director',merge=False)
    assert outcome['next_role']=='audience'
    done(c,chatpath(pid,ts['audience']))
    outcome,_=agree(c,pid,ts['audience'],'audience')
    assert outcome['next_role']=='writer'
    done(c,chatpath(pid,ts['writer']))
    assert [r['role'] for r in runner.calls[-1][-1]['handoff_package']['upstream']]==['director','audience']
    c.close()


def test_confirmation_failure_rolls_back_acceptance_merge_and_budget(tmp_path):
    runner,c,pid,ts=setup(tmp_path,budget=1);view=begin(c,pid,ts['director'])
    before=c.get('/api/projects/'+pid).json()
    agree(c,pid,ts['director'],'no-budget',merge=True,status=409)
    assert c.get('/api/projects/'+pid).json()==before and len(runner.calls)==1
    agree(c,pid,ts['director'],'no-risk-ack',merge=True,start=False,status=422,acknowledged_uncertainties=False)
    assert c.get('/api/projects/'+pid).json()==before
    # A user can confirm and navigate without paying for a first reply.
    result,_=agree(c,pid,ts['director'],'no-paid-start',merge=True,start=False)
    assert result['next_role']=='writer' and result['job_id'] is None
    assert len(runner.calls)==1
    c.close()


def test_stale_message_and_brief_cannot_advance(tmp_path):
    runner,c,pid,ts=setup(tmp_path);first=begin(c,pid,ts['director'])
    post(c,chatpath(pid,ts['director']),{'message':'改结尾','max_cost':1},'more')
    done(c,chatpath(pid,ts['director']))
    before=c.get('/api/projects/'+pid).json()
    agree(c,pid,ts['director'],'stale',merge=True,status=409,message_id=first['messages'][-1]['id'])
    agree(c,pid,ts['director'],'stale-brief',merge=True,status=409,expected_brief_revision=999)
    assert c.get('/api/projects/'+pid).json()==before and len(runner.calls)==2
    c.close()


def test_upstream_change_invalidates_inherited_director_input(tmp_path):
    runner,c,pid,ts=setup(tmp_path);begin(c,pid,ts['director'])
    agree(c,pid,ts['director'],'director')
    done(c,chatpath(pid,ts['audience']))
    agree(c,pid,ts['audience'],'audience')
    done(c,chatpath(pid,ts['writer']))
    store=Store(tmp_path)
    with store.transaction() as db:
        director=store.task(db,ts['director']['id'],pid)
        artifact=store.record(db,pid,'artifact',director['result_id'])
        store.save_record(db,pid,'artifact',{**artifact['data'],'body':'改过的方向'},artifact['id'],artifact['version'],artifact['input_refs'],skip_task=director['id'])
    assert c.get('/api'+chatpath(pid,ts['writer'])).json()['task']['status']=='needs_review'
    assert c.get(f"/api/projects/{pid}/tasks/{ts['writer']['id']}/handoff").status_code==409
    agree(c,pid,ts['writer'],'invalidated',status=409)
    assert len(runner.calls)==3
    c.close()


def test_existing_audience_work_cannot_be_merged_and_final_click_is_explicit(tmp_path):
    runner,c,pid,ts=setup(tmp_path);begin(c,pid,ts['director'])
    result,data=agree(c,pid,ts['director'],'director',merge=False)
    done(c,chatpath(pid,ts['audience']))
    before=c.get('/api/projects/'+pid).json()
    agree(c,pid,ts['director'],'unsafe-merge',merge=True,status=409)
    assert c.get('/api/projects/'+pid).json()==before and len(runner.calls)==2
    agree(c,pid,ts['audience'],'not-confirmed',status=422,confirmed=False)
    assert c.get('/api/projects/'+pid).json()==before
    c.close()


def test_missing_hypotheses_do_not_get_fabricated(tmp_path):
    runner,c,pid,ts=setup(tmp_path)
    def no_assumptions(*args):
        runner.calls.append(args)
        return RoleResult(title='方向',summary='仅讨论',body='海盗方向')
    c.app.state.runtime.run=no_assumptions
    begin(c,pid,ts['director'])
    before=c.get('/api/projects/'+pid).json()
    agree(c,pid,ts['director'],'missing-hypothesis',merge=True,status=422)
    assert c.get('/api/projects/'+pid).json()==before and len(runner.calls)==1
    c.close()

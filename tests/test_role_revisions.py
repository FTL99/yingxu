from studio.models import RoleResult
from studio.store import Store
from studio.handoff import edge
from tests.test_advance import setup, begin, agree, chatpath
from tests.test_chat import post, done


def prepared(tmp_path):
    runner,c,pid,ts=setup(tmp_path,budget=100)
    begin(c,pid,ts['director'])
    for role in ('director','writer','assets','storyboard'):
        outcome,_=agree(c,pid,ts[role],role,merge=role=='director')
        done(c,chatpath(pid,ts[outcome['next_role']]))
    return runner,c,pid,ts


def reopen(c,pid,task,rid='reopen',status=200,**extra):
    view=c.get('/api'+chatpath(pid,task)).json()
    return post(c,f"/projects/{pid}/tasks/{task['id']}/reopen",{
        'expected_brief_revision':1,'artifact_version':view['artifact_version'],**extra},rid,status)


def change(runner):
    def run(*args):
        runner.calls.append(args)
        role=args[-1]['task']['role']
        return RoleResult(title=role+'新版设定',summary='主角改为女船长，保留原海盗故事和节奏',
                          body='新版完整方案：主角为女船长；保留原海盗主线，高潮改成她指挥反攻。',uncertainties=['真实观众反应未验证'])
    runner.run=run


def test_return_to_writer_confirm_sync_and_each_role_adapts_without_media_regeneration(tmp_path):
    runner,c,pid,ts=prepared(tmp_path)
    store=Store(tmp_path)
    with store.transaction() as db:
        writer=store.task(db,ts['writer']['id'])
        original=store.record(db,pid,'artifact',writer['result_id'])
        bible=store.save_record(db,pid,'bible',{'name':'旧船长'},refs=[edge(original)])
        asset=store.save_record(db,pid,'asset',{'name':'旧图','media_type':'image','source':'api_generation'},refs=[edge(original)])
    before=c.get('/api/projects/'+pid).json()
    reopened=reopen(c,pid,ts['writer'])
    assert reopened['task_id']==ts['writer']['id'] and len(runner.calls)==5
    assert c.get('/api'+chatpath(pid,ts['writer'])).json()['artifact_version']==1
    agree(c,pid,ts['writer'],'no-new-reply',status=409)
    change(runner)
    post(c,chatpath(pid,ts['writer']),{'message':'主角改为女船长，保留原主线','max_cost':1,'media_budget':0},'new-writer')
    done(c,chatpath(pid,ts['writer']))
    context=runner.calls[-1][-1]
    assert context['revision_mode'] and any('海盗' in turn['content'] for turn in context['conversation'][:-1])
    assert not any(r['id'] in (original['id'],bible['id'],asset['id']) for r in context['inputs'])
    balance=c.get('/api/projects/'+pid).json()['balance']
    result,data=agree(c,pid,ts['writer'],'sync-writer')
    assert result['settings_synced'] and result['job_id'] is None
    assert result['artifact_ref']=={**edge(original),'version':2}
    assert result['next_role']=='assets'
    assert {r['role'] for r in result['synced_agents']}=={'director','assets','storyboard','post'}
    assert {r['role'] for r in result['synced_agents'] if r['needs_update']}=={'assets','storyboard','post'}
    assert len(runner.calls)==6 and c.get('/api/projects/'+pid).json()['balance']==balance
    assert post(c,f"/projects/{pid}/tasks/{ts['writer']['id']}/advance",data,'sync-writer')==result
    after=c.get('/api/projects/'+pid).json()
    assert after['media_jobs']==before['media_jobs']==[]
    assert next(r for r in after['records'] if r['id']==asset['id'])['stale']
    history=c.get(f"/api/projects/{pid}/records/artifact/{original['id']}/history").json()
    assert len(history)==2 and '女船长' in history[0]['data']['body'] and history[1]['data']==original['data']
    for role in ('assets','storyboard','post'):
        view=c.get('/api'+chatpath(pid,ts[role])).json()
        assert view['task']['sync_pending'] and view['task']['status']=='discussing'
        assert view['messages'][-1]['role']=='user' and '设定同步' in view['messages'][-1]['text']
        assert view['sync_changes'][0]['artifact_ref']==result['artifact_ref']
    # A blocked role can discuss the new plan, but cannot approve stale upstream work or generate media.
    post(c,chatpath(pid,ts['storyboard']),{'message':'先讨论新设定对镜头的影响','max_cost':1},'early-storyboard')
    done(c,chatpath(pid,ts['storyboard']))
    early=runner.calls[-1][-1]
    assert early['handoff_package']['pending_upstream']
    assert not early['media_authorization']['enabled']
    assert '女船长' in early['project_changes'][0]['body']
    agree(c,pid,ts['storyboard'],'early-sync',status=409)
    for role in ('assets','storyboard'):
        post(c,chatpath(pid,ts[role]),{'message':'承接女船长设定，更新本阶段方案','max_cost':1,'media_budget':0},'adapt-'+role)
        done(c,chatpath(pid,ts[role]))
        outcome,_=agree(c,pid,ts[role],'sync-'+role)
        assert outcome['settings_synced'] and outcome['job_id'] is None
        with store.connect() as db:
            task=store.task(db,ts[role]['id'])
            assert task['status']=='accepted' and not task['sync_pending']
            artifact=store.record(db,pid,'artifact',task['result_id'])
            assert artifact['version']==2 and not artifact['stale']
            store.validate_refs(db,pid,artifact['input_refs'])
    assert c.get('/api/projects/'+pid).json()['media_jobs']==[]
    c.close()


def test_reopen_checks_versions_and_running_work_preserves_old_contract(tmp_path):
    runner,c,pid,ts=prepared(tmp_path)
    before=c.get('/api/projects/'+pid).json()
    reopen(c,pid,ts['writer'],rid='wrong-version',status=409,artifact_version=99)
    reopen(c,pid,ts['writer'],rid='wrong-brief',status=409,expected_brief_revision=99)
    assert c.get('/api/projects/'+pid).json()==before
    store=Store(tmp_path)
    with store.transaction() as db:
        db.execute("UPDATE tasks SET status='running' WHERE id=?",(ts['post']['id'],))
    reopen(c,pid,ts['writer'],rid='busy',status=409)
    with store.connect() as db:
        assert store.task(db,ts['writer']['id'])['status']=='accepted'
    assert len(runner.calls)==5
    c.close()


def test_merged_audience_reopens_the_actual_director_conversation(tmp_path):
    runner,c,pid,ts=prepared(tmp_path)
    result=reopen(c,pid,ts['audience'])
    assert result['task_id']==ts['director']['id'] and result['role']=='director'
    store=Store(tmp_path)
    with store.connect() as db:
        assert store.task(db,ts['audience']['id'])['status']=='merged'
        assert store.task(db,ts['director']['id'])['revision_open']
    assert len(runner.calls)==5
    c.close()


def test_director_revision_updates_gate_and_does_not_reuse_old_evidence(tmp_path):
    runner,c,pid,ts=prepared(tmp_path)
    store=Store(tmp_path)
    with store.connect() as db:
        old=store.records(db,pid,'hypothesis')[0]
    reopen(c,pid,ts['director']);change(runner)
    post(c,chatpath(pid,ts['director']),{'message':'把目标观众和主角设定改为新版本','max_cost':1},'new-director')
    view=done(c,chatpath(pid,ts['director']))
    assert view['advance']['assumptions'][0]['claim']==old['data']['claim']
    assert any('历史证据' in risk for risk in view['advance']['risks'])
    outcome,_=agree(c,pid,ts['director'],'sync-director')
    assert outcome['next_role']=='writer' and outcome['job_id'] is None
    with store.connect() as db:
        current=store.record(db,pid,'hypothesis',old['id'])
        assert current['version']==2 and current['data']['evidence_status']=='untested'
        assert current['data']['evidence']=='' and not current['stale']
        assert store.record(db,pid,'hypothesis',old['id'],1)['data']==old['data']
        g0=next(r for r in store.records(db,pid,'decision') if r['data'].get('stage')=='G0')
        assert g0['version']==2 and not g0['stale']
        store.validate_refs(db,pid,g0['input_refs'])
        assert all(store.task(db,ts[role]['id'])['sync_pending'] for role in ('writer','assets','storyboard','post'))
    c.close()


def test_post_text_revision_sync_does_not_accept_final_film(tmp_path):
    runner,c,pid,ts=prepared(tmp_path)
    # Seed a prior post approval solely to exercise reopening; real approval requirements stay intact.
    store=Store(tmp_path)
    with store.transaction() as db:
        task=store.task(db,ts['post']['id'])
        result=store.save_record(db,pid,'artifact',{'title':'后期','summary':'旧后期方案','body':'旧后期方案','brief_revision':1,'final_movie':False},refs=task['input_refs'])
        db.execute("UPDATE tasks SET status='accepted',result_id=? WHERE id=?",(result['id'],task['id']))
    reopen(c,pid,ts['post']);change(runner)
    post(c,chatpath(pid,ts['post']),{'message':'调整配乐和结尾字幕','max_cost':1},'new-post')
    done(c,chatpath(pid,ts['post']))
    outcome,_=agree(c,pid,ts['post'],'sync-post')
    assert outcome['final_review_required'] and outcome['settings_synced'] and outcome['job_id'] is None
    with store.connect() as db:
        task=store.task(db,ts['post']['id'])
        assert task['status']=='pending_review'
        assert not store.record(db,pid,'artifact',task['result_id'])['data']['final_movie']
        assert not any(r['data'].get('stage')=='G3' and not r['stale'] for r in store.records(db,pid,'decision'))
    c.close()

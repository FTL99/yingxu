"""Persistent role discussion. A reply is a draft until explicitly finalized."""
import json

from fastapi import Header

from .models import ChatInput, FinalizeChat, Reconcile, RoleResult, ConfirmStage, ReviewInput, ReopenStage
from .prompts import role_prompt, ROLE_META, presentation_rules
from .handoff import upstream_package, append_upstream_refs, proposed_hypotheses, edge
from .capabilities import capabilities, CAPABILITY_RULES
from .results import read_output
from .store import DomainError, dump, now, uid
from .storyboard_tools import register_storyboard_tools, media_context, tool_rules, execute_actions
from .revisions import editing, discussion_refs, project_changes, ensure_idle


CHAT_RULES='''
\n对话模式补充（保留六项职责和JSON交付合同）：
你正在与人类持续讨论当前阶段，优先直接回应最新问题和反馈。
body使用易读的Markdown，以短段落为主，通常300至800字；只在比较时使用小表格。
每轮先给判断及其理由，再处理分歧，最多提出两个具体问题。不要重复整份报告或铺开下游任务。
summary是一句话摘要。以历史讨论为上下文，用户明确选择过的决定要记住，不要反复询问。
预算单位CNY，不能判断已发生费用；工作流事实以workflow_snapshot为准，阶段仅G0至G3。
mode=finalize时，将已讨论并达成的决定整理成当前角色的可执行阶段成果：已确认决定、关键假设与未验证事项、交给下一角色的要点。
不要把用户表示喜欢或模型猜测当作真实观众证据；缺少确认必须标注。人类决定何时验收和推进。
handoff_package包含全部已验收上游成果及确认时讨论，须沿用人类决定和约束，不重新询问已确认的问题。
流程状态只能由工作台修改。没有实际确认记录时，不能声称已写入版本库、修改任务、放行或联系下游。
阶段决定已收敛时，说明可以点击“无异议，确认并交接”；不要求人类自己切标签或录入任务表。
接棒第一轮先用几句话复述继承的决定，再直接开始本角色工作，不重复已有Brief或重新发散上游已选方向。
revision_mode=true时，用户正在回来修改原设定。保留原讨论和明确未变的选择，针对修改反馈给出新版完整阶段方案（可用短表说明改了什么及影响哪些角色），不要只留下差异片段。
project_changes是人类已确认同步的新版设定；优先于历史对话中同主题的旧选择。pending_upstream表示上游未重新确认，可先讨论本角色的调整，但不能把旧成果称为有效交接或声称已经同步、重新验收。
回改方案收敛时提示点击“确认修改并同步其他 Agent”。同步本身不调用模型、不重新生图或生视频；已有媒体保留待复查，只有用户明确要求生成才按媒体工具规则执行。
只返回RoleResult JSON对象，body是给用户看的回答。不要展示内部JSON、思考过程或重复Brief。
'''


def register_chat(app,store,runtime,executor,ready,submit,balance,review_task,validate_stage):
    register_storyboard_tools(app,store)
    def messages(db,pid,tid):
        return [{**dict(row),'result':json.loads(row['result']) if row['result'] else None,'complete':bool(row['complete']),'initial':row['job_id'] is None,
                 'system_notice':row['role']=='user' and row['job_id'] is None and row['text'].startswith(('【设定同步】','【工作台记录】'))}
                for row in db.execute('SELECT * FROM chat_messages WHERE project_id=? AND task_id=? ORDER BY rowid',(pid,tid))]

    def job(db,pid,jid):
        row=db.execute('SELECT * FROM chat_jobs WHERE id=? AND project_id=?',(jid,pid)).fetchone()
        if not row: raise DomainError('对话调用不存在',404)
        return dict(row)

    def history(db,task):
        rows=messages(db,task['project_id'],task['id'])
        if rows: return rows
        initial=[]
        if task['result_id']:
            artifact=store.record(db,task['project_id'],'artifact',task['result_id'])
            # The current artifact can already originate from a saved chat.
            if artifact['data'].get('source')!='role_chat':
                initial=[{'id':f"initial-{artifact['id']}-{artifact['version']}",'role':'assistant',
                          'text':artifact['data']['body'],'result':artifact['data'],'complete':True,'initial':True}]
        elif task.get('session_id'):
            output=read_output(store.root,task)
            if output['available']:
                initial=[{'id':'initial-'+task['id'],'role':'assistant','text':output['draft_fields']['body'] or output['text'],
                          'result':output['structured'],'complete':output['complete'],'initial':True,'warnings':output['warnings']}]
        return initial+rows

    @app.get('/api/projects/{pid}/tasks/{tid}/chat')
    def conversation(pid:str,tid:str):
        with store.connect() as db:
            # Read task, messages and jobs from the same SQLite snapshot.
            db.execute('BEGIN')
            task=store.task(db,tid,pid)
            rows=history(db,task)
            jobs=[dict(r) for r in db.execute('SELECT * FROM chat_jobs WHERE project_id=? AND task_id=? ORDER BY rowid',(pid,tid))]
            artifact=store.record(db,pid,'artifact',task['result_id']) if task['result_id'] else None
            hypotheses=[h for h in store.records(db,pid,'hypothesis') if not h['stale']]
            latest=rows[-1].get('result') if rows else None
            candidates=proposed_hypotheses(latest or (artifact or {}).get('data',{}),task['role'])
            refresh_hypotheses=[]
            if editing(task):
                _,excluded=discussion_refs(store,db,task,[edge(h) for h in hypotheses])
                ids={q['id'] for q in excluded}
                refresh_hypotheses=[h for h in hypotheses if h['id'] in ids]
            risks=list((latest or (artifact or {}).get('data',{})).get('uncertainties',[]))
            if refresh_hypotheses:
                risks.append('设定修改后，关联假设与历史证据需重新验证；旧版记录保留，新版不会继续沿用已验证结论。')
            media_calls=app.state.storyboard_calls(db,pid,tid)
            return {'messages':rows,'jobs':jobs,'media_calls':media_calls,'media_running':any(c['status'] in ('running','queued') or any(i['status']=='running' for i in (c.get('result') or {}).get('batch',{}).get('items',[])) for c in media_calls),
                    'running':any(j['status']=='running' for j in jobs),'task':task,
                    'artifact_version':artifact['version'] if artifact else 0,
                    'sync_changes':project_changes(store,db,pid),
                    'advance':{'assumptions':candidates if not hypotheses or (refresh_hypotheses and candidates) else [h['data'] for h in refresh_hypotheses],
                               'risks':risks,
                               'unverified_hypotheses':sum(h['data'].get('evidence_status')!='supported' for h in hypotheses)}}

    @app.get('/api/projects/{pid}/tasks/{tid}/handoff')
    def handoff(pid:str,tid:str):
        with store.connect() as db:
            db.execute('BEGIN')
            task=store.task(db,tid,pid)
            return upstream_package(store,db,task,allow_pending=editing(task))

    @app.post('/api/projects/{pid}/tasks/{tid}/reopen')
    def reopen(pid:str,tid:str,value:ReopenStage,x_request_id:str=Header(default='')):
        with store.transaction() as db:
            def operation():
                task=store.task(db,tid,pid)
                # A merged audience role's decisions live in the director's conversation.
                if task.get('merged_into'):
                    task=store.task(db,task['merged_into'],pid)
                if task.get('superseded_by'):
                    raise DomainError('此任务已被替代，请选择当前角色任务。')
                ensure_idle(db,pid)
                if app.state.image_batch_active(db,pid,task['id']):
                    raise DomainError('请先完成或取消此阶段的图片队列，再修改设定。')
                if store.project(db,pid)['revision']!=value.expected_brief_revision or task['brief_revision']!=value.expected_brief_revision:
                    raise DomainError('Brief已变化，请使用当前Brief建立修改任务。')
                version=store.record(db,pid,'artifact',task['result_id'])['version'] if task['result_id'] else 0
                if task['id']==tid and version!=value.artifact_version:
                    raise DomainError('成果版本已变化，请刷新后修改。')
                if editing(task): return {'task_id':task['id'],'role':task['role'],'reopened':True}
                if task['status'] not in ('accepted','needs_review','changes_requested'):
                    raise DomainError('当前阶段可以直接讨论，无需重新开启。')
                refs,excluded=discussion_refs(store,db,task,task['input_refs'])
                # Opening a conversation does not overwrite the previous approved artifact.
                if not messages(db,pid,task['id']):
                    for row in history(db,task):
                        db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                                   (uid('message'),pid,task['id'],None,row['role'],row['text'],dump(row['result']) if row.get('result') else None,int(row['complete']),now()))
                baseline=messages(db,pid,task['id'])
                db.execute("UPDATE tasks SET status='discussing',revision_open=1,revision_message_id=?,input_refs=?,error=NULL,updated_at=? WHERE id=?",(baseline[-1]['id'] if baseline else None,dump(refs),now(),task['id']))
                store.event(db,pid,'role.reopened',{'task_id':task['id'],'role':task['role'],'base_version':version,'excluded_refs':excluded})
                return {'task_id':task['id'],'role':task['role'],'reopened':True}
            return store.idempotent(db,pid,'role.reopen',x_request_id,{'tid':tid,**value.model_dump()},operation)

    def worker(pid,tid,jid,sid,prompt,context):
        try:
            result=runtime.run(jid,sid,prompt,context)
            visible=RoleResult.model_validate(result.model_dump(exclude={'media_actions'}))
            with store.transaction() as db:
                task=store.task(db,tid,pid)
                current=job(db,pid,jid)
                if current['status']!='running' or task['status']!='running':
                    raise DomainError('讨论期间输入变化，保留草稿，请先核对当前版本。')
                db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                           (uid('message'),pid,tid,jid,'assistant',visible.body,dump(visible.model_dump()),1,now()))
            if getattr(result,'media_actions',[]):
                execute_actions(app,store,pid,tid,jid,context,result)
            with store.transaction() as db:
                task=store.task(db,tid,pid)
                if task['status']!='running':
                    raise DomainError('讨论期间输入变化，媒体原任务会保留，请核对最新版本。')
                db.execute("UPDATE chat_jobs SET status='completed' WHERE id=?",(jid,))
                db.execute("UPDATE tasks SET status='discussing',error=NULL,updated_at=? WHERE id=?",(now(),tid))
                store.event(db,pid,'chat.replied',{'task_id':tid,'job_id':jid})
            app.state.start_image_batches(pid,tid,jid)
        except Exception as error:
            safe=error.message if isinstance(error,DomainError) else f'{type(error).__name__}：对话未完成；不会自动重试。'
            output=read_output(store.root,{'id':jid,'session_id':sid,'status':'interrupted'})
            with store.transaction() as db:
                db.execute("UPDATE chat_jobs SET status='interrupted',error=? WHERE id=?",(safe,jid))
                if output['available']:
                    db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                               (uid('message'),pid,tid,jid,'assistant',output['draft_fields']['body'] or output['text'],None,0,now()))
                task=store.task(db,tid,pid)
                if task['status']=='running':
                    db.execute("UPDATE tasks SET status='interrupted',error=?,updated_at=? WHERE id=?",(safe,now(),tid))
                elif task['status']=='input_changed':
                    db.execute("UPDATE tasks SET status='needs_review',error=?,updated_at=? WHERE id=?",(safe,now(),tid))
                store.event(db,pid,'chat.interrupted',{'task_id':tid,'job_id':jid,'message':safe})
                db.execute("UPDATE image_batches SET status='paused',error='讨论未完整结束，队列暂停；请核对会话，不自动生成。' WHERE chat_job_id=? AND status='queued'",(jid,))

    def queue_turn(db,pid,tid,value):
        task=store.task(db,tid,pid)
        if task.get('superseded_by') or task['status'] not in ('draft','discussing','pending_review','changes_requested','interrupted'):
            raise DomainError('此阶段不可继续讨论；已验收阶段需要建立局部返工任务。')
        if not value.message.strip(): raise DomainError('请输入你想讨论的内容',422)
        can_generate=True
        if editing(task):
            if store.project(db,pid)['revision']!=task['brief_revision']:
                raise DomainError('Brief已变化，请使用当前Brief建立修改任务。')
            refs,excluded=discussion_refs(store,db,task,task['input_refs'])
            task={**task,'input_refs':refs}
            store.validate_refs(db,pid,refs)
            try: ready(db,task)
            except DomainError: can_generate=False
        else:
            excluded=[]
            ready(db,task)
        if not runtime.status()['ready']: raise DomainError('请先配置并启用DeepSeek',422)
        b=balance(db,pid)
        if b['actual']+b['reserved']+value.max_cost>b['budget']:
            raise DomainError('项目余额不足，无法预留本轮额度')
        if db.execute("SELECT 1 FROM chat_jobs WHERE task_id=? AND status='running'",(tid,)).fetchone():
            raise DomainError('Agent正在回复，请等本轮结束。')
        prompt=role_prompt(task['role'])
        if prompt['version']!=task['prompt_version']: raise DomainError('提示词已更新，请建立新任务。')
        refs=list(task['input_refs'])
        package=upstream_package(store,db,task,allow_pending=editing(task))
        append_upstream_refs(refs,package)
        for dep in task['dependencies']:
            upstream=store.task(db,dep,pid)
            if editing(task): continue  # Only the verified entries in the partial package are authoritative.
            artifact=store.record(db,pid,'artifact',upstream['result_id'])
            input_ref={'kind':'artifact','id':artifact['id'],'version':artifact['version']}
            if input_ref not in refs: refs.append(input_ref)
        contextual=[r for r in store.records(db,pid) if r['kind'] in ('hypothesis','reference','experiment','decision','lesson','bible','asset','shot') and not r['stale']]
        if editing(task):
            valid,omitted=discussion_refs(store,db,task,[edge(r) for r in contextual])
            excluded.extend(omitted)
            allowed={(r['kind'],r['id']) for r in valid}
            contextual=[r for r in contextual if (r['kind'],r['id']) in allowed]
        for r in contextual:
            input_ref={'kind':r['kind'],'id':r['id'],'version':r['version']}
            if input_ref not in refs: refs.append(input_ref)
        changes=project_changes(store,db,pid)
        shared_refs,_=discussion_refs(store,db,task,[c['artifact_ref'] for c in changes if c['confirmed']])
        for input_ref in shared_refs:
            if input_ref not in refs: refs.append(input_ref)
        turns=history(db,task)
        turns.append({'role':'user','text':value.message.strip()})
        if sum(len(r['text']) for r in turns)>160000:
            raise DomainError('本阶段讨论过长，请先整理已有决定，再建立局部任务。')
        context={'mode':value.mode,'revision_mode':editing(task),'project_changes':changes,
            'excluded_historical_refs':excluded,'project':{**store.project(db,pid),'budget_currency':'CNY'},'task':task,
            'capabilities':capabilities(),
            'handoff_package':package,
            'conversation':[{'role':r['role'],'content':r['text'],'complete':r.get('complete',True)} for r in turns],
            'inputs':[store.record(db,pid,r['kind'],r['id'],r['version']) for r in refs],
            'workflow_snapshot':[{k:t[k] for k in ('id','role','status','dependencies','merged_into')} for t in store.tasks(db,pid)],
            'phase_contract':{'G0':'方向与受众说明确认（可由总控承接）','G1':'编剧方案确认','G2':'分镜方案确认；批量生成前仍需真实成本校验','G3':'真实成片五项人工验收'},
            'tools':[],'media_providers':app.state.media_catalog(),'output_contract':RoleResult.model_json_schema()}
        context.update(media_context(app,db,pid,tid,task['role'],value.media_budget,value.mode if can_generate else 'finalize'))
        jid=uid('chat');sid=uid('session')
        db.execute('INSERT INTO chat_jobs(id,project_id,task_id,session_id,status,reservation,created_at) VALUES(?,?,?,?,?,?,?)',
                   (jid,pid,tid,sid,'running',value.max_cost,now()))
        if not messages(db,pid,tid):
            for row in turns[:-1]:
                db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                           (uid('message'),pid,tid,None,'assistant',row['text'],dump(row['result']) if row.get('result') else None,int(row['complete']),now()))
        db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                   (uid('message'),pid,tid,jid,'user',value.message.strip(),None,1,now()))
        db.execute("UPDATE tasks SET status='running',input_refs=?,error=NULL,updated_at=? WHERE id=?",(dump(refs),now(),tid))
        # Local input snapshot supports recovery; no credential is stored.
        folder=store.root/'task-inputs';folder.mkdir(exist_ok=True)
        (folder/f'{jid}.json').write_text(dump({'prompt_version':prompt['version'],'mode':'chat-v1','context':context}),encoding='utf-8')
        store.event(db,pid,'chat.sent',{'task_id':tid,'job_id':jid,'reserved':value.max_cost})
        launch=(pid,tid,jid,sid,prompt['text']+CHAT_RULES+presentation_rules(task['role'])+CAPABILITY_RULES+tool_rules(task['role']),context)
        return job(db,pid,jid),launch

    @app.post('/api/projects/{pid}/tasks/{tid}/chat')
    def send(pid:str,tid:str,value:ChatInput,x_request_id:str=Header(default='')):
        launch=None
        with store.transaction() as db:
            def operation():
                nonlocal launch
                result,launch=queue_turn(db,pid,tid,value)
                return result
            result=store.idempotent(db,pid,'chat.send',x_request_id,{'tid':tid,**value.model_dump()},operation)
        if launch: executor.submit(worker,*launch)
        return result

    @app.post('/api/projects/{pid}/tasks/{tid}/chat/finalize')
    def finalize(pid:str,tid:str,value:FinalizeChat,x_request_id:str=Header(default='')):
        with store.transaction() as db:
            def operation():
                task=store.task(db,tid,pid)
                if app.state.image_batch_active(db,pid,tid):
                    raise DomainError('请先完成或取消图片队列，再保存阶段成果。')
                if task['status']!='discussing': raise DomainError('请等待完整回复后再保存阶段成果。')
                rows=messages(db,pid,tid)
                if not rows or rows[-1]['id']!=value.message_id or rows[-1]['role']!='assistant' or not rows[-1]['complete']:
                    raise DomainError('讨论版本已变化，请查看最新完整回复。')
                old=store.record(db,pid,'artifact',task['result_id'])['version'] if task['result_id'] else 0
                result=RoleResult.model_validate(rows[-1]['result'])
                return submit(db,task,result,old,'role_chat')
            return store.idempotent(db,pid,'chat.finalize',x_request_id,{'tid':tid,**value.model_dump()},operation)

    @app.post('/api/projects/{pid}/tasks/{tid}/advance')
    def advance(pid:str,tid:str,value:ConfirmStage,x_request_id:str=Header(default='')):
        launch=None
        with store.transaction() as db:
            def operation():
                nonlocal launch
                task=store.task(db,tid,pid)
                sync_mode=editing(task)
                if sync_mode: ensure_idle(db,pid)
                baseline_hypotheses=[h for h in store.records(db,pid,'hypothesis') if not h['stale']] if sync_mode else []
                if app.state.image_batch_active(db,pid,tid):
                    raise DomainError('请先完成或取消图片队列，再确认交接。')
                if task['role']=='post' and not sync_mode:
                    raise DomainError('最终成片须保留五项实际人工验收，不能通过文字确认直接放行。')
                if task.get('superseded_by') or task.get('merged_into') or task['status'] not in ('discussing','pending_review','accepted'):
                    raise DomainError('请等待完整回复，输入变化或中断时不能确认交接。')
                if store.project(db,pid)['revision']!=value.expected_brief_revision:
                    raise DomainError('Brief已变化，请刷新后确认。')
                ready(db,task)
                current=store.record(db,pid,'artifact',task['result_id']) if task['result_id'] else None
                if (current['version'] if current else 0)!=value.artifact_version:
                    raise DomainError('成果版本已变化，请刷新后确认。')
                rows=history(db,task)
                if sync_mode and task.get('revision_message_id') and (not rows or rows[-1]['id']==task['revision_message_id']):
                    raise DomainError('请先与 Agent 讨论修改并得到新回复，再确认同步。')
                previous_ref=edge(current) if current else None
                if task['status']=='discussing':
                    if not rows or rows[-1]['id']!=value.message_id or rows[-1]['role']!='assistant' or not rows[-1]['complete'] or not rows[-1].get('result'):
                        raise DomainError('请选择最新完整回复，不能确认旧版讨论。')
                    current=submit(db,task,RoleResult.model_validate(rows[-1]['result']),value.artifact_version,'role_chat')
                    task=store.task(db,tid,pid)
                elif value.message_id and (not rows or rows[-1]['id']!=value.message_id):
                    raise DomainError('讨论版本已变化，请查看最新回复。')
                if task['status']=='pending_review' and task['role']!='post':
                    review_task(db,task,ReviewInput(outcome='accepted',acknowledged_uncertainties=value.acknowledged_uncertainties,
                        note='用户点击无异议确认交接；只确认文字阶段方案，不代表观众或成本已验证。',
                        checks=[{'criterion':'阶段方案符合本人确认的选择','status':'passed','note':'用户确认当前成果版本，并授权作为下一角色的输入。','evidence_refs':[edge(current)]}]))
                    task=store.task(db,tid,pid)
                merged=[]
                if value.merge_audience and not sync_mode:
                    if task['role']!='director': raise DomainError('仅总控可以承接受众策略。',422)
                    for audience in store.tasks(db,pid):
                        if audience['role']!='audience' or audience.get('superseded_by') or audience.get('merged_into') or tid not in audience['dependencies']:
                            continue
                        children=[t for t in store.tasks(db,pid) if audience['id'] in t['dependencies']]
                        touched=[audience,*children]
                        if any(t['status']!='draft' or t['result_id'] or t['session_id'] or db.execute('SELECT 1 FROM chat_jobs WHERE task_id=?',(t['id'],)).fetchone() for t in touched):
                            raise DomainError('观众策略或下游已有执行记录，不能直接合并；请保留原交接或建立局部任务。')
                        for child in children:
                            deps=list(dict.fromkeys(tid if d==audience['id'] else d for d in child['dependencies']))
                            db.execute('UPDATE tasks SET dependencies=?,updated_at=? WHERE id=?',(dump(deps),now(),child['id']))
                        db.execute("UPDATE tasks SET status='merged',merged_into=?,updated_at=? WHERE id=?",(tid,now(),audience['id']))
                        merged.append(audience['id'])
                    store.event(db,pid,'audience.merged',{'director_task':tid,'tasks':merged,'authorization':'human-stage-confirmation','no_audience_validation':True})
                stage={'audience':'G0','writer':'G1','storyboard':'G2'}.get(task['role'])
                if task['role']=='director' and any(t.get('merged_into')==tid for t in store.tasks(db,pid)): stage='G0'
                if stage:
                    hypotheses=[h for h in store.records(db,pid,'hypothesis') if not h['stale']]
                    if not hypotheses:
                        proposed=proposed_hypotheses(current['data'],task['role'])
                        if (proposed or baseline_hypotheses) and not value.acknowledged_uncertainties:
                            raise DomainError('候选假设仍未验证，请确认愿意带着这些风险继续。',422)
                        for data in proposed:
                            store.save_record(db,pid,'hypothesis',data,refs=[edge(current)])
                        if not proposed:
                            for old in baseline_hypotheses:
                                latest_h=store.record(db,pid,'hypothesis',old['id'])
                                if not latest_h['stale']: continue
                                store.save_record(db,pid,'hypothesis',{**old['data'],'evidence_status':'untested','evidence':'',
                                    'revision_note':'设定已修改，历史证据保留在旧版；本版本需要重新验证。'},
                                    old['id'],latest_h['version'],[edge(current)])
                    previous=next((r for r in store.records(db,pid,'decision') if r['data'].get('stage')==stage),None)
                    refs=[edge(current)]
                    data={'stage':stage,'allow':True,'risks_acknowledged':value.acknowledged_uncertainties,
                          'note':'用户明确确认阶段方案并授权交接；不代表观众有效性、工具可行性或成本已经验证。'}
                    if not previous or previous['stale'] or edge(current) not in previous['input_refs'] or not previous['data'].get('allow'):
                        validate_stage(db,pid,data,refs,previous['id'] if previous else None)
                        store.save_record(db,pid,'decision',data,previous['id'] if previous else None,previous['version'] if previous else 0,refs)
                synced=[]
                if sync_mode:
                    # Propagate task dependencies as well as recorded artifact edges.
                    # New reviews/gates belonging to this source remain valid.
                    affected=store.invalidate(db,pid,{('task',tid)},skip_task=tid)
                    for recipient in store.tasks(db,pid):
                        if recipient['id']==tid or recipient.get('superseded_by') or recipient.get('merged_into'): continue
                        changed=('task',recipient['id']) in affected
                        old_history=history(db,recipient)
                        if not messages(db,pid,recipient['id']):
                            for row in old_history:
                                db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                                           (uid('message'),pid,recipient['id'],None,row['role'],row['text'],dump(row['result']) if row.get('result') else None,int(row['complete']),now()))
                        if changed:
                            valid,_=discussion_refs(store,db,recipient,recipient['input_refs'])
                            has_history=bool(old_history)
                            db.execute("UPDATE tasks SET sync_pending=1,status=?,input_refs=?,error=NULL,updated_at=? WHERE id=?",
                                       ('discussing' if has_history else 'draft',dump(valid),now(),recipient['id']))
                        confirmed=task['role']!='post'
                        description=current['data']['summary'][:2000].strip().rstrip('。.!！？?')
                        notice=f"【设定同步】{ROLE_META[task['role']][1]}的修改{'已确认' if confirmed else '已保存，成片仍须人工验收'}（V{current['version']}）：{description}。"
                        notice+=('本环节的旧成果需要更新，请在下轮讨论中承接新设定，保留未受影响的决定。' if changed else '请在后续讨论中核对这项新设定对本角色的影响。')
                        notice+='已有图片和视频保留；此次同步未重新生成或验证媒体。'
                        db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)',
                                   (uid('message'),pid,recipient['id'],None,'user',notice,None,1,now()))
                        synced.append({'task_id':recipient['id'],'role':recipient['role'],'needs_update':changed})
                    db.execute("UPDATE tasks SET revision_open=0,sync_pending=0,revision_message_id=NULL WHERE id=?",(tid,))
                    store.event(db,pid,'role.settings_synced',{'task_id':tid,'role':task['role'],'artifact_ref':edge(current),
                        'previous_ref':previous_ref,'summary':current['data']['summary'],'confirmed':task['role']!='post','recipients':synced})
                priority={'audience':0,'writer':1,'assets':2,'storyboard':3,'post':4}
                children=[t for t in store.tasks(db,pid) if tid in t['dependencies'] and not t.get('merged_into') and not t.get('superseded_by') and t['status']!='accepted']
                children.sort(key=lambda t:priority.get(t['role'],10))
                next_task=None
                for child in children:
                    try: ready(db,child)
                    except DomainError: continue
                    next_task=child
                    break
                if children and not next_task and not sync_mode:
                    raise DomainError('下一环节仍有未完成的依赖或阶段条件，请先处理后再交接。')
                next_job=None
                if next_task and value.start_next and next_task['status']=='draft' and not sync_mode:
                    next_job,launch=queue_turn(db,pid,next_task['id'],ChatInput(max_cost=value.max_cost,media_budget=0,
                        message=f"我已确认{ROLE_META[task['role']][1]}阶段的方案。请承接随附的已确认资料、讨论和未验证事项，简短复述继承的决定，然后开始本阶段工作，不重复已经确认的问题。"))
                package=upstream_package(store,db,next_task,allow_pending=editing(next_task)) if next_task else None
                result={'confirmed_task_id':tid,'artifact_ref':edge(current),'next_task_id':next_task['id'] if next_task else None,
                        'next_role':next_task['role'] if next_task else None,'job_id':next_job['id'] if next_job else None,
                        'handoff_refs':[r['artifact_ref'] for r in package['upstream']] if package else [],'merged_tasks':merged,
                        'settings_synced':sync_mode,'synced_agents':synced,'final_review_required':sync_mode and task['role']=='post'}
                store.event(db,pid,'stage.advanced',result)
                return result
            result=store.idempotent(db,pid,'stage.advance',x_request_id,{'tid':tid,**value.model_dump()},operation)
        if launch: executor.submit(worker,*launch)
        return result

    @app.post('/api/projects/{pid}/chat/{jid}/reconcile')
    def reconcile_chat(pid:str,jid:str,value:Reconcile,x_request_id:str=Header(default='')):
        with store.transaction() as db:
            def operation():
                current=job(db,pid,jid)
                if current['status']=='running': raise DomainError('本轮尚未结束，不能核账。')
                db.execute('UPDATE chat_jobs SET actual_cost=?,cost_evidence=? WHERE id=?',(value.actual_cost,value.evidence,jid))
                store.event(db,pid,'chat.reconciled',{'job_id':jid,'actual_cost':value.actual_cost})
                return job(db,pid,jid)
            return store.idempotent(db,pid,'chat.reconcile',x_request_id,{'jid':jid,**value.model_dump()},operation)

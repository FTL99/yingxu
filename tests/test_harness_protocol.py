"""A local, scripted SSE server tests SDK plumbing, not model intelligence.

No real credential, external provider, or paid inference is involved.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

from studio.models import RuntimeConfig, RoleResult, StoryboardReply
from studio.prompts import role_prompt
from studio.runtime import HarnessRuntime
from studio.results import read_output
from studio.store import DomainError


@pytest.mark.parametrize('visible',['','{"title":"草稿","summary":"摘要","body":"可见正文"}'])
def test_transport_error_reports_network_without_disclosing_sdk_data(tmp_path,monkeypatch,visible):
    from types import SimpleNamespace
    calls=[]
    class FakeHarness:
        def __init__(self,config): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def run(self,*args,**kwargs):
            calls.append(1)
            return SimpleNamespace(final_response=visible,finish_reason='error',events=[
                {'type':'turn/end','data':{'reason':{'kind':'error','error':{'code':'TRANSPORT','message':'PRIVATE-KEY private provider body'}}}}])
    monkeypatch.setattr('deepseek_harness.DeepSeekHarness',FakeHarness)
    runtime=HarnessRuntime(tmp_path)
    runtime.save(RuntimeConfig(enabled=True,api_key='local-test-only'))
    with pytest.raises(DomainError) as exc:
        runtime.run('transport-test','transport-session','测试',{})
    assert '网络连接失败' in exc.value.message and 'TRANSPORT' in exc.value.message
    assert 'PRIVATE' not in exc.value.message and len(calls)==1
    assert ('未收到可见回答' if not visible else '已保留可见草稿') in exc.value.message
    saved=read_output(tmp_path,{'id':'transport-test','status':'interrupted'})
    assert saved['text']==visible and not saved['complete']


@pytest.mark.parametrize('role',['director','storyboard'])
def test_official_sdk_with_local_scripted_response(tmp_path,role):
    received=[]
    value=RoleResult(title='本地协议测试',summary='预设响应，非模型创作',body='仅检查SDK与结果合同').model_dump_json()
    if role=='storyboard':
        value=StoryboardReply(title='本地协议测试',summary='预设响应，非模型创作',body='仅检查SDK与结果合同',
            media_actions=[{'tool':'get_media_result','action_id':'read','job_id':'fixture-job'}]).model_dump_json()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(payload)
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            for delta,finish in [({'role':'assistant','content':value},None),({},'stop')]:
                chunk={'id':'local-protocol','object':'chat.completion.chunk','created':1,'model':'deepseek-v4-flash','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    runtime=HarnessRuntime(tmp_path)
    runtime.save(RuntimeConfig(enabled=True,api_key='local-test-placeholder',base_url=f'http://127.0.0.1:{server.server_port}/v1',request_timeout_seconds=30))
    try:
        context={'project':{'name':'测试替身'}}
        if role=='storyboard': context['tool_contract']='storyboard-media-v1'
        result=runtime.run('protocol-test','protocol-session',role_prompt(role)['text'],context)
        assert result.title=='本地协议测试'
        assert received and not received[0].get('tools')
        assert '任务目标（Goal）' in json.dumps(received[0],ensure_ascii=False)
        assert received[0]['max_tokens']==16384
        assert received[0]['reasoning_effort']=='low'
        if role=='storyboard':
            assert isinstance(result,StoryboardReply) and result.media_actions[0].job_id=='fixture-job'
            output=read_output(tmp_path,{'id':'protocol-test','session_id':'protocol-session','status':'completed'})
            assert output['complete'] and 'media_actions' not in output['structured']
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('finish,exception',[('length',DomainError),('stop',ValueError)])
def test_sdk_preserves_incomplete_visible_output(tmp_path,finish,exception):
    received=[]
    text='{"title":"未完成","summary":"实际预算100 USD，未发生费用","body":"第一行\\n第二行'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            for delta,end in [({'role':'assistant','reasoning_content':'PRIVATE-REASONING'},None),({'content':text},None),({},finish)]:
                chunk={'id':'local-truncation','object':'chat.completion.chunk','created':1,'model':'deepseek-v4-flash','choices':[{'index':0,'delta':delta,'finish_reason':end}]}
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    runtime=HarnessRuntime(tmp_path)
    runtime.save(RuntimeConfig(enabled=True,api_key='local-test-only',base_url=f'http://127.0.0.1:{server.server_port}/v1',request_timeout_seconds=30))
    try:
        with pytest.raises(exception):
            runtime.run('truncated-test','truncated-session',role_prompt('director')['text'],{'project':{'budget':100,'budget_currency':'CNY'}})
        view=read_output(tmp_path,{'id':'truncated-test','session_id':'truncated-session','status':'interrupted'})
        assert view['text']==text and not view['complete'] and view['structured'] is None
        assert view['draft_fields']['body']=='第一行\n第二行'
        assert len(view['warnings'])>=3
        assert 'PRIVATE-REASONING' not in json.dumps(view)
        assert len(received)==1  # No automatic replay.
    finally:
        server.shutdown();server.server_close()

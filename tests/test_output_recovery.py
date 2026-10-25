import json

from studio.results import partial_fields, read_output


def test_partial_strings_preserve_prefix_without_invention():
    assert partial_fields('{"title":"标题","body":"第一行\\n第二行\\u4f60\\u12')=={'title':'标题','body':'第一行\n第二行你'}
    assert partial_fields('{"title":')=={}
    assert partial_fields('这不是JSON')=={}


def test_legacy_session_recovery_filters_private_blocks(tmp_path):
    folder=tmp_path/'harness'/'task-test'/'sessions'/'workspace'/'session-test'
    folder.mkdir(parents=True)
    rows=[{'type':'request/header','data':{'Authorization':'FAKE-KEY-PRIVATE'}},
          {'type':'assistant/message','data':{'message':{'content':[{'type':'reasoning','text':'PRIVATE-THOUGHT'},{'type':'text','text':'{"title":"草稿","summary":"摘要","body":"前半段'}]},'usage':{'output_tokens':8192,'private':'FAKE-KEY-PRIVATE'}}},
          {'type':'turn/end','data':{'reason':{'kind':'max-tokens'}}}]
    (folder/'session.v3.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows),encoding='utf-8')
    task={'id':'task-test','session_id':'session-test','status':'interrupted'}
    view=read_output(tmp_path,task)
    assert view['draft_fields']['body']=='前半段' and not view['complete']
    assert view['usage']=={'output_tokens':8192}
    assert 'PRIVATE' not in json.dumps(view)
    assert read_output(tmp_path,task)==view

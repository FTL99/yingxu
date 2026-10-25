import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.gemini import ENDPOINT, GeminiSettings, GeminiVideoAdapter, MAX_INLINE_BYTES
from studio.models import GeminiConfig
from studio.store import DomainError


KEY = "placeholder-gemini-test-only"


def local_client(root):
    client = TestClient(create_app(root))
    client.headers['X-Studio-Token'] = client.get('/api/health').json()['token']
    return client


def test_independent_key_save_preserve_clear_and_restart(tmp_path):
    with local_client(tmp_path) as client:
        assert not client.get('/api/gemini').json()['has_key']
        client.put('/api/runtime', json={'api_key': 'placeholder-deepseek-test-only'})
        saved = client.put('/api/gemini', json={'api_key': KEY, 'fps': 2, 'max_output_tokens': 2048})
        assert saved.status_code == 200 and KEY not in saved.text
        assert saved.json()['has_key'] and not saved.json()['ready'] and not saved.json()['enabled']
        assert 'api_key' not in saved.json()
        assert client.get('/api/capabilities').json()['audience_video_validation']['enabled'] is False
        assert client.put('/api/gemini', json={'api_key': '', 'fps': 2}).json()['has_key']
        # Neither runtime's public status nor invalid Gemini configuration echoes credentials.
        assert KEY not in client.get('/api/runtime').text
        invalid = client.put('/api/gemini', json={'api_key': KEY, 'enabled': True})
        assert invalid.status_code == 422 and KEY not in invalid.text
        assert client.get('/api/gemini').json()['enabled'] is False
        invalid = client.put('/api/gemini', json={'api_key': KEY, 'fps': 0})
        assert invalid.status_code == 422 and KEY not in invalid.text
    with local_client(tmp_path) as restored:
        assert restored.get('/api/gemini').json()['has_key']
        assert restored.get('/api/gemini').json()['fps'] == 2
        result = restored.put('/api/gemini', json={'clear_key': True})
        assert not result.json()['has_key'] and not result.json()['enabled']
        assert restored.get('/api/runtime').json()['has_key']
    assert KEY not in (tmp_path/'gemini-settings.json').read_text()
    assert 'placeholder-deepseek-test-only' not in (tmp_path/'gemini-settings.json').read_text()


def test_disabled_blocks_before_keys_media_network_and_budget(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled analysis must not read credentials or send a request')
    with local_client(tmp_path) as client:
        client.app.state.gemini_video.settings.config = forbidden
        client.app.state.gemini_video.transport = httpx.MockTransport(forbidden)
        pid = client.post('/api/projects', json={'name': '禁用测试'}, headers={'X-Request-ID': 'project'}).json()['id']
        before = client.get('/api/projects/'+pid).json()
        blocked = client.post(f'/api/projects/{pid}/video-analysis', json={'asset_id': 'no-file', 'prompt': '评审'})
        assert blocked.status_code == 409 and '禁用' in blocked.text
        with pytest.raises(DomainError, match='禁用'):
            client.app.state.gemini_video.analyze(b'video', 'video/mp4', '评审')
        after = client.get('/api/projects/'+pid).json()
        assert before == after
        assert not (tmp_path/'gemini-settings.json').exists()


def test_gemini_protocol_and_visible_output_only(tmp_path):
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, json={
            'id': 'interaction-test', 'status': 'completed',
            'steps': [
                {'type': 'user_input', 'content': [{'type': 'text', 'text': 'do not echo'}]},
                {'type': 'thought', 'summary': [{'type': 'text', 'text': 'hidden-thought'}]},
                {'type': 'model_output', 'content': [{'type': 'text', 'text': '00:03 字幕遮挡人物。'},
                                                     {'type': 'thought', 'text': 'hidden-thought'}]},
            ],
            'usage': {'total_input_tokens': 6000, 'total_output_tokens': 1000,
                      'total_thought_tokens': 300, 'secret': KEY, 'total_tool_use_tokens': -1},
        })
    adapter = GeminiVideoAdapter(GeminiSettings(tmp_path), httpx.MockTransport(reply))
    config = GeminiConfig(api_key=KEY)
    result = adapter._request_analysis(b'local-video-fixture', 'video/mp4', '评审故事和字幕', config)
    assert len(requests) == 1 and str(requests[0].url) == ENDPOINT
    assert requests[0].headers['x-goog-api-key'] == KEY and KEY not in str(requests[0].url)
    payload = json.loads(requests[0].content)
    assert KEY not in json.dumps(payload)
    assert payload['input'][0]['data'] == base64.b64encode(b'local-video-fixture').decode()
    assert payload['input'][0]['processing'] == {'type': 'static', 'fps': 1}
    assert payload['tools'] == [] and payload['store'] is False
    assert payload['generation_config']['thinking_summaries'] == 'none'
    assert payload['generation_config']['max_output_tokens'] == 3072
    assert result['text'] == '00:03 字幕遮挡人物。'
    assert 'hidden-thought' not in json.dumps(result) and KEY not in json.dumps(result)
    assert result['usage'] == {'total_input_tokens': 6000, 'total_output_tokens': 1000, 'total_thought_tokens': 300}
    assert result['audience_behavior_verified'] is False and result['evidence_type'] == 'model_inference'


@pytest.mark.parametrize('failure', ['timeout', 'http', 'incomplete', 'invalid-json', 'empty'])
def test_failures_never_retry_or_echo_provider_body(tmp_path, failure):
    calls = []
    def fail(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout(KEY, request=request)
        if failure == 'http':
            return httpx.Response(401, text=KEY)
        if failure == 'incomplete':
            return httpx.Response(200, json={'status': 'failed', 'error': KEY})
        if failure == 'invalid-json':
            return httpx.Response(200, text=KEY)
        return httpx.Response(200, json={'status': 'completed', 'steps': []})
    adapter = GeminiVideoAdapter(GeminiSettings(tmp_path), httpx.MockTransport(fail))
    with pytest.raises(DomainError) as error:
        adapter._request_analysis(b'fixture', 'video/mp4', '评审', GeminiConfig(api_key=KEY))
    assert len(calls) == 1 and KEY not in str(error.value.message)
    assert '不会自动重试' in error.value.message


def test_inline_limit_rejects_before_network(tmp_path):
    adapter = GeminiVideoAdapter(GeminiSettings(tmp_path), httpx.MockTransport(lambda r: pytest.fail('no network')))
    with pytest.raises(DomainError, match='12MB'):
        adapter._request_analysis(b'x'*(MAX_INLINE_BYTES+1), 'video/mp4', '评审', GeminiConfig(api_key=KEY))

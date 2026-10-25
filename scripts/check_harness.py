"""Offline startup check. Initializes SDK; never submits a prompt."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from deepseek_harness import DeepSeekHarness
from studio.runtime import HarnessRuntime
from studio.prompts import role_prompt

root=Path(__file__).resolve().parent.parent/'data'
runtime=HarnessRuntime(root)
config=runtime.build_config('offline-handshake',role_prompt('director')['text'],dict(provider='deepseek-official',model='deepseek-v4-flash',api_key='offline-placeholder-not-a-real-key',base_url='',request_timeout_seconds=30))
with DeepSeekHarness(config):
    print('PASS: official SDK initialized; no inference request or real credential used.')

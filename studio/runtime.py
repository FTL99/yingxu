import importlib.metadata
import json
import os
import threading
from pathlib import Path

from .models import RuntimeConfig, RoleResult, StoryboardReply
from .store import DomainError
from .results import output_view, save_output


class HarnessRuntime:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "runtime-settings.json"
        self.lock = threading.Lock()

    def config(self):
        if self.path.exists():
            return RuntimeConfig.model_validate_json(self.path.read_text(encoding="utf-8")).model_dump()
        return RuntimeConfig().model_dump()

    def status(self):
        c = self.config()
        try:
            version = importlib.metadata.version("deepseek-harness-sdk")
        except importlib.metadata.PackageNotFoundError:
            version = None
        key = c.get("api_key") or os.environ.get("DEEPSEEK_API_KEY")
        return {**{k: v for k, v in c.items() if k not in ("api_key", "clear_key")}, "has_key": bool(key), "sdk_version": version,
                "ready": bool(c["enabled"] and key and version), "media_generation": "分镜讨论可按本轮素材额度生成，其他角色通过手动入口确认调用", "billing": "手工核账；预留额度不是供应商硬限额"}

    def save(self, value: RuntimeConfig):
        with self.lock:
            old = self.config()
            c = value.model_dump()
            if not c["clear_key"] and not c["api_key"]:
                c["api_key"] = old.get("api_key")
            if c["clear_key"]:
                c["api_key"] = None
            c["clear_key"] = False
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)
        return self.status()

    def build_config(self, task_id, prompt, credentials=None):
        from deepseek_harness import DeepSeekHarnessConfig
        c = credentials or self.config()
        home = self.root / "harness" / task_id
        work = home / "work"
        work.mkdir(parents=True, exist_ok=True)
        # sdk-minimal has two persistent shell tools. Disable both explicitly;
        # no web, filesystem or subagent tools exist in this profile.
        patch = home / "studio.patch.json"
        patch.write_text(json.dumps([
            {"id": "sandbox", "disabled": True},
            {"id": "sandbox-policy", "disabled": True},
            {"id": "subprocess", "disabled": True},
            {"id": "pty", "disabled": True},
            {"id": "terminal-bash", "disabled": True},
            {"id": "terminal-pwsh", "disabled": True},
            {"id": "persistent-bash", "disabled": True},
            {"id": "persistent-pwsh", "disabled": True},
            {"id": "system-prompt", "config": {"includeHarnessIdentity": False, "includeRuntimeContext": False, "personaPrefix": prompt, "personaSuffix": ""}},
        ], ensure_ascii=False), encoding="utf-8")
        return DeepSeekHarnessConfig(
            provider=c["provider"], model=c["model"], cwd=str(work), runtime_cwd=str(work),
            profile="sdk-minimal", patches=(str(patch.resolve()),), dsh_home=str(home.resolve()),
            api_key=c.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"), base_url=c.get("base_url") or None,
            env={"DSH_TELEMETRY_MODE": "OFF"}, max_tokens=c.get("max_output_tokens",16384),
            reasoning_effort=c.get("reasoning_effort","low"),
            request_timeout_seconds=c["request_timeout_seconds"],
        )

    def run(self, task_id, session_id, prompt, context):
        from deepseek_harness import DeepSeekHarness
        if not self.status()["ready"]:
            raise DomainError("尚未启用 DeepSeek 或未配置 API Key", 422)
        with DeepSeekHarness(self.build_config(task_id, prompt)) as harness:
            result = harness.run(json.dumps(context, ensure_ascii=False), session_id=session_id)
        # Do not publish hidden reasoning or credentials through event streaming.
        usage={}
        for event in result.events:
            if event.get("type")=="assistant/message":
                usage={k:v for k,v in (event.get("data",{}).get("usage") or {}).items() if isinstance(v,(int,float))}
        # Persist only human-readable RoleResult fields; tool intents are separately
        # validated and journaled by the conversation worker before any media call.
        visible = result.final_response
        parsed = None
        if result.finish_reason == "completed" and context.get("tool_contract") == "storyboard-media-v1":
            try:
                parsed = StoryboardReply.model_validate_json(result.final_response)
            except Exception:
                save_output(self.root,task_id,output_view(visible,result.finish_reason,usage))
                raise
            visible = RoleResult.model_validate(parsed.model_dump(exclude={"media_actions"})).model_dump_json()
        save_output(self.root,task_id,output_view(visible,result.finish_reason,usage))
        if result.finish_reason != "completed":
            if result.finish_reason=="max-tokens":
                raise DomainError("回答达到长度上限，已保留未完成草稿。可查看并补齐，或主动重新生成；本次费用仍需核账。")
            # Read only the SDK's terminal error code, never provider messages,
            # headers, request context or hidden reasoning from session events.
            terminal = next((event for event in reversed(result.events) if event.get("type") == "turn/end"), {})
            reason = terminal.get("data", {}).get("reason", {})
            error = reason.get("error", {}) if isinstance(reason, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            preserved = "已保留可见草稿；" if visible.strip() else "本次未收到可见回答；"
            if code == "TRANSPORT":
                raise DomainError("DeepSeek网络连接失败（TRANSPORT）：未收到正常API响应，请检查工作台进程的网络权限、代理或连接。" + preserved + "不会自动重新发起任务。")
            raise DomainError(f"运行未完整结束：{result.finish_reason}。" + preserved + "不会自动重试。")
        return parsed if parsed is not None else RoleResult.model_validate_json(result.final_response)

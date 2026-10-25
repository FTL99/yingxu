"""Explicit media API boundaries. Saving credentials never sends a request."""
import base64
import hashlib
import ipaddress
import json
import math
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, urlunparse, quote

import httpx
import imageio_ffmpeg
from PIL import Image
from fastapi import Header

from .models import MediaConfig, MediaRequest, MediaGenerate, Reconcile
from .store import DomainError, dump, uid, now
from .media import has_audio_stream

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_MEDIA = 100 * 1024 * 1024
MAX_REFERENCE = 10 * 1024 * 1024
PRICE_DATE = "2026-10-03"


def media_http_diagnostic(response):
    """Keep known codes and a support request ID, never an arbitrary body."""
    reasons = {
        "ModelNotOpen": "model_not_open",
        "InvalidEndpointOrModel.NotFound": "model_not_found",
        "InvalidEndpointOrModel.ModelIDAccessDisabled": "model_id_disabled",
        "AuthenticationError": "authentication",
        "OperationDenied.ServiceOverdue": "overdue",
        "SetLimitExceeded": "inference_limit",
        "ModelAccountIpmRateLimitExceeded": "image_rate",
        "RateLimitExceeded.EndpointRPMExceeded": "request_rate",
        "RateLimitExceeded.EndpointTPMExceeded": "token_rate",
        "AccountRateLimitExceeded": "request_rate",
        "InflightBatchsizeExceeded": "concurrency",
        "RequestBurstTooFast": "burst",
        "ServerOverloaded": "overloaded",
        "QuotaExceeded": "quota_unspecified",
        "MissingParameter": "parameter_missing",
        "MissingParameter.": "parameter_missing",
        "InvalidParameter": "parameter_invalid",
        "InvalidParameter.": "parameter_invalid",
        "InvalidParameter.UnsupportedParameter": "unsupported_parameter",
        "InvalidParameter.TaskTypeConstraint": "task_type_invalid",
        "InvalidParameter.TaskTypeMismatch": "task_type_invalid",
        "InvalidEndpoint.ClosedEndpoint": "endpoint_closed",
        "InputTextSensitiveContentDetected": "input_text_review",
        "InputTextSensitiveContentDetected.PolicyViolation": "input_text_review",
        "SensitiveContentDetected": "input_text_review",
        "SensitiveContentDetected.SevereViolation": "input_text_review",
        "SensitiveContentDetected.Violence": "input_text_review",
        "InputTextRiskDetection": "input_text_review",
        "InputImageSensitiveContentDetected": "input_image_review",
        "InputImageSensitiveContentDetected.PolicyViolation": "input_image_review",
        "InputImageSensitiveContentDetected.PrivacyInformation": "input_image_review",
        "InputImageRiskDetection": "input_image_review",
        "OutputImageSensitiveContentDetected": "output_image_review",
        "OutputImageSensitiveContentDetected.DeepFake": "output_image_review",
        "OutputImageRiskDetection": "output_image_review",
        "InputVideoSensitiveContentDetected": "input_video_review",
        "InputVideoSensitiveContentDetected.PolicyViolation": "input_video_review",
        "InputVideoSensitiveContentDetected.PrivacyInformation": "input_video_review",
        "OutputVideoSensitiveContentDetected": "output_video_review",
        "OutputVideoSensitiveContentDetected.PolicyViolation": "output_video_review",
        "ContentSecurityDetectionError": "review_service_error",
    }
    body = {}
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
    except (ValueError, TypeError):
        error = {}
    code = error.get("code")
    code = code if isinstance(code, str) and code in reasons else None
    reason = reasons.get(code)
    message = str(error.get("message", "")).lower()
    if code == "QuotaExceeded":
        if "free trial quota" in message or "free quota" in message:
            reason = "free_quota"
        elif "queue" in message or "queuing" in message:
            reason = "queued_tasks"
    request_id = response.headers.get("x-request-id")
    if not request_id and isinstance(body, dict):
        request_id = body.get("request_id")
    if not request_id:
        match = re.search(r"request\s*(?:id|_id)\s*:\s*([A-Za-z0-9_-]{1,128})", str(error.get("message", "")), re.IGNORECASE)
        request_id = match.group(1) if match else None
    try:
        key = response.request.headers.get("authorization", "").removeprefix("Bearer ")
    except RuntimeError:
        key = ""
    if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
            or key and key in request_id):
        request_id = None
    return {"http_status": response.status_code, "code": code, "reason": reason, "request_id": request_id}


def media_http_error(response, diagnostic=None):
    """Translate known provider codes; never expose arbitrary error bodies."""
    messages = {
        "ModelNotOpen": "当前账号尚未开通所选模型。请到火山方舟控制台「开通管理」开通对应模型服务；保存Key不等于开通模型。",
        "InvalidEndpointOrModel.NotFound": "模型或推理接入点不存在，或当前账号无权访问；请核对模型ID、接入点及账号权限。",
        "InvalidEndpointOrModel.ModelIDAccessDisabled": "当前账号不允许通过模型ID调用；请在火山方舟控制台核对推理接入点与调用方式。",
        "AuthenticationError": "API Key缺失或无效；请核对所用平台和已保存的Key。",
        "OperationDenied.ServiceOverdue": "供应商账号余额欠费；请到官方控制台核对账单与余额。",
    }
    diagnostic = media_http_diagnostic(response) if diagnostic is None else diagnostic
    code = diagnostic["code"]
    limited = {
        "inference_limit": "账号为该模型设置的推理限额已达到，模型服务已暂停。请到火山方舟「开通管理」核对推理限额/安心体验模式；仅等待或减少并发不能解除此限制。调整限额可能产生后续付费调用，请由你决定。",
        "image_rate": "账号对该模型的每分钟图片数（IPM）已超限；请降低出图频率，或在控制台核对IPM配额。",
        "request_rate": "账号或推理接入点的请求频率已超限；请核对RPM配额并降低请求频率。",
        "token_rate": "推理接入点的每分钟Token数（TPM）已超限；请核对TPM配额。",
        "concurrency": "账号当前可用的并发额度已达到；请核对其它调用中的任务与并发配额。",
        "burst": "请求量增长触发供应商突发流量保护；请放缓提交频率。",
        "overloaded": "供应商服务资源紧张；请稍后再发起，不能据此认定账号欠费。",
        "free_quota": "该模型免费试用额度已用尽；请在开通管理核对免费额度与付费开通状态。不会自动切换付费模式。",
        "queued_tasks": "账号排队中的任务数已达到限制；请先核对已有任务。",
        "quota_unspecified": "供应商返回配额超限，但未提供可识别的限制类型；请凭请求编号向供应商核对。",
    }
    rejected = {
        "parameter_missing": "生成请求缺少必要参数；请核对该任务的接口请求体，不要直接原样重试。",
        "parameter_invalid": "供应商判定请求参数不合法；请核对模型、尺寸、参考图和字段取值，凭请求编号查看具体参数错误。",
        "unsupported_parameter": "请求包含当前模型或接入点不支持的参数；请核对该任务的接口请求体。",
        "task_type_invalid": "请求参数与供应商识别的任务类型不兼容；请核对提示词、参考素材和参数。",
        "endpoint_closed": "所用推理接入点已关闭或暂不可用；请核对接入点状态。",
        "input_text_review": "供应商的输入文本审核未通过；请查看提示词和审核说明，再决定如何修改内容。此错误不表示Key无效或余额不足。",
        "input_image_review": "供应商的参考图片审核未通过；请查看所用参考图与审核说明，再决定如何更换素材。",
        "output_image_review": "生成图片未通过供应商的输出审核，未返回可用图片；请查看审核说明，费用以供应商账单为准。",
        "input_video_review": "供应商的输入视频审核未通过；请查看参考视频与审核说明。",
        "output_video_review": "生成视频未通过供应商的输出审核；请查看审核说明，费用以供应商账单为准。",
        "review_service_error": "供应商的内容审核服务请求失败；这不等于判定内容违规，请凭请求编号核对服务状态。",
    }
    if isinstance(code, str) and code in messages:
        detail = f" · {code}：{messages[code]}"
    elif diagnostic["reason"] in rejected:
        detail = f" · {code}：{rejected[diagnostic['reason']]}"
    elif response.status_code == 429 and diagnostic["reason"] in limited:
        detail = f" · {code}：{limited[diagnostic['reason']]}"
    elif response.status_code == 404:
        detail = "：接口、模型或任务不存在，或当前账号无权访问；请核对接口路径和模型开通状态。"
    elif response.status_code == 401:
        detail = "：鉴权失败，请核对已保存的API Key及其所属平台。"
    elif response.status_code == 429:
        detail = "：请求受到限流或配额限制。请在供应商控制台核对调用频率与配额，避免连续或并发提交；稍后再发起。"
    elif response.status_code == 400:
        detail = "：供应商拒绝本次请求，具体错误码未识别；需凭请求编号核对参数校验或内容审核记录，不能仅凭400判断账号权限或余额。"
    else:
        detail = "；请在供应商控制台核对权限、任务与账单。"
    support = f" 请求编号：{diagnostic['request_id']}。" if diagnostic["request_id"] else ""
    return f"媒体供应商返回HTTP {response.status_code}{detail}{support} 核对费用；不会自动重试。"


def public_url(value, *, resolve=False):
    """No credentials, fragments, local addresses or redirects in API destinations."""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise DomainError("地址端口无效", 422) from None
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.fragment or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
            or port not in (None, 443)):
        raise DomainError("地址须为公网 HTTPS，不能包含账号密码或本机地址", 422)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise DomainError("地址不能指向本机或内网", 422)
    if resolve and not address:
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise DomainError("目标地址无法解析；未自动重试", 502) from None
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise DomainError("目标地址解析到非公网地址", 422)
    return parsed


def config_version(config):
    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()


def ali_base(config):
    return (f"https://{config.workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1"
            if config.workspace_id else "https://dashscope.aliyuncs.com/api/v1")


def require_openrouter(config):
    relay = config.seedance
    if relay.base_url.rstrip("/") != OPENROUTER_BASE:
        raise DomainError("OpenRouter协议必须使用 https://openrouter.ai/api/v1", 422)
    if not relay.model.startswith("bytedance/seedance-"):
        raise DomainError("请填写OpenRouter的Seedance模型名，例如 bytedance/seedance-2.0", 422)


class MediaSettings:
    def __init__(self, root):
        self.path = Path(root) / "media-settings.json"
        self.lock = threading.Lock()

    def config(self):
        with self.lock:
            return self._read()

    def _read(self):
        return (MediaConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
                if self.path.exists() else MediaConfig())

    def status(self, config=None):
        config = self.config() if config is None else config
        result = {}
        for name in ("seedream", "aliyun", "seedance"):
            value = getattr(config, name)
            result[name] = {**value.model_dump(exclude={"api_key", "clear_key"}), "has_key": bool(value.api_key)}
        result["seedream"]["ready"] = bool(config.seedream.api_key)
        result["aliyun"]["ready"] = bool(config.aliyun.api_key)
        result["seedance"]["ready"] = bool(config.seedance.api_key and config.seedance.base_url
            and config.seedance.model and config.seedance.protocol in ("ark", "openrouter") and config.seedance.price_per_second is not None)
        result["message"] = "配置仅保存到本机；未验证Key。点击确认生成才发送素材并计费。"
        return result

    def save(self, value):
        if value.seedance.base_url:
            parsed = public_url(value.seedance.base_url)
            if parsed.query:
                raise DomainError("中转站API地址不能包含查询参数或Key", 422)
        with self.lock:
            old = self._read()
            saved = {}
            for name in ("seedream", "aliyun", "seedance"):
                previous = getattr(old, name)
                if name not in value.model_fields_set:
                    saved[name] = previous
                    continue
                patch = getattr(value, name)
                new = previous.model_copy(update={k: getattr(patch, k) for k in patch.model_fields_set})
                key = (patch.api_key or "").strip()
                if key and (any(ch.isspace() for ch in key) or not key.isascii()):
                    raise DomainError("API Key 须为不含空白的ASCII字符串", 422)
                # A relay credential is never silently forwarded to a changed destination.
                if (name == "seedance" and new.base_url.rstrip("/") != previous.base_url.rstrip("/")
                        and previous.api_key and not key and not new.clear_key):
                    raise DomainError("中转站地址已改变，请重新填写该站Key或清除旧Key", 422)
                saved[name] = new.model_copy(update={"api_key": None if new.clear_key else key or previous.api_key,
                                                    "clear_key": False})
            config = MediaConfig(**saved)
            if config.seedance.protocol == "openrouter":
                require_openrouter(config)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(config.model_dump_json(), encoding="utf-8")
            temporary.replace(self.path)
        return self.status()

    def catalog(self):
        state = self.status()
        return [
            {"id": "seedream", "name": "Seedream 生图", "model": state["seedream"]["model"],
             "ready": state["seedream"]["ready"], "reference_images": "0–10张", "seed": False,
             "negative_prompt": False, "lora": False, "price": "0.12元/张", "price_date": PRICE_DATE},
            {"id": "wan", "name": "万相 图生视频", "model": state["aliyun"]["video_model"],
             "ready": state["aliyun"]["ready"], "reference_images": "1张首帧", "seed": True,
             "negative_prompt": True, "lora": False, "price": "720P无声0.15元/秒；1080P无声0.25元/秒", "price_date": PRICE_DATE},
            {"id": "cosyvoice", "name": "CosyVoice 配音", "model": state["aliyun"]["tts_model"],
             "ready": state["aliyun"]["ready"], "voice": state["aliyun"]["voice"], "lora": False},
            {"id": "seedance", "name": "Seedance 中转站", "model": state["seedance"]["model"],
             "ready": state["seedance"]["ready"], "protocol": state["seedance"]["protocol"],
             "price_per_second": state["seedance"]["price_per_second"],
             "audio_price_per_second": state["seedance"]["audio_price_per_second"],
             "audio_supported": state["seedance"]["protocol"] == "openrouter",
             "billing": "OpenRouter以美元按模型SKU计费；元/秒仅用户预算参考" if state["seedance"]["protocol"] == "openrouter" else "用户填写中转站报价",
             "scope": "720P文生视频/单首帧；OpenRouter可选声音，生成前查询模型支持；方舟兼容仍仅无声。美元token费用另行核账"},
        ]


def estimate(value, config):
    if not value.prompt.strip():
        raise DomainError("请填写画面描述或配音文本", 422)
    if len(set(value.reference_ids)) != len(value.reference_ids):
        raise DomainError("参考素材不能重复选择", 422)
    if value.audio_direction.strip() and (not value.audio or value.provider not in ("wan", "seedance")):
        raise DomainError("声音设计仅用于勾选生成声音的视频请求", 422)
    if value.provider == "seedream":
        if value.seed is not None or value.negative_prompt or value.audio:
            raise DomainError("当前Seedream适配器不提供seed、负面提示词或音频参数", 422)
        amount, model = .12, config.seedream.model
    elif value.provider == "wan":
        if len(value.reference_ids) != 1:
            raise DomainError("万相图生视频需要选择1张首帧图片", 422)
        rate = {("720P", False): .15, ("1080P", False): .25, ("720P", True): .3, ("1080P", True): .5}
        amount, model = value.duration * rate[value.resolution, value.audio], config.aliyun.video_model
    elif value.provider == "cosyvoice":
        if value.reference_ids or value.seed is not None or value.negative_prompt or value.audio:
            raise DomainError("配音不接收图片、seed或视频参数", 422)
        if config.aliyun.tts_model.startswith("cosyvoice-v3.5") and config.aliyun.voice.startswith("long"):
            raise DomainError("CosyVoice3.5没有系统音色，请填写该模型的设计/克隆音色ID，或选择3 Plus", 422)
        rate = {"cosyvoice-v3-plus": 2, "cosyvoice-v3-flash": 1,
                "cosyvoice-v3.5-plus": 1.5, "cosyvoice-v3.5-flash": .8}
        amount, model = len(value.prompt) / 10000 * rate[config.aliyun.tts_model], config.aliyun.tts_model
    else:
        if config.seedance.protocol not in ("ark", "openrouter") or not config.seedance.base_url or not config.seedance.model:
            raise DomainError("Seedance中转站尚未确定协议、API地址和模型名；请先配置", 422)
        if config.seedance.price_per_second is None:
            raise DomainError("请填写中转站的720P预算参考单价；不能套用官方价格", 422)
        if config.seedance.protocol == "openrouter":
            require_openrouter(config)
        if len(value.reference_ids) > 1 or value.resolution != "720P" or value.negative_prompt:
            raise DomainError("中转站支持720P、文字或单张首帧；不支持负面提示词", 422)
        if value.audio and config.seedance.protocol != "openrouter":
            raise DomainError("方舟兼容中转站尚未核实声音能力；请使用OpenRouter协议", 422)
        rate = config.seedance.audio_price_per_second if value.audio else config.seedance.price_per_second
        if rate is None:
            raise DomainError("请在运行设置填写720P有声视频预算参考价；不会沿用无声价或假定翻倍", 422)
        if value.duration < 4:
            raise DomainError("当前Seedance视频适配器支持4至15秒，请核对中转站模型范围", 422)
        amount, model = value.duration * rate, config.seedance.model
    return {"provider": value.provider, "model": model, "estimated_cost": round(amount, 6),
            "currency": "CNY", "audio_requested": value.audio,
            "price_date": PRICE_DATE if value.provider != "seedance" else "用户填写",
            "note": ("OpenRouter按视频token以美元计费；这里使用用户填写的元/秒预算参考价，未核验其等价性。实际美元费用需按账单换算核对。"
                     if value.provider == "seedance" and config.seedance.protocol == "openrouter" else
                     "估算不是账单或厂商硬限额；成功生成但未采用也可能收费。")}


def image_data_uri(store, pid, asset):
    if asset["data"].get("media_type") != "image":
        raise DomainError("生成参考须为本项目已导入的图片", 422)
    path = Path(asset["data"]["path"]).resolve()
    if not path.is_relative_to((store.root / "media" / pid).resolve()) or not path.is_file():
        raise DomainError("参考图片路径无效", 422)
    if path.stat().st_size > MAX_REFERENCE:
        raise DomainError("每张参考图片最多10MB，请先缩小图片", 422)
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower())
    if not mime:
        raise DomainError("参考图片须为PNG/JPG/WebP", 422)
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class MediaAdapter:
    def __init__(self, transport=None):
        self.transport = transport

    def request(self, method, url, key, payload=None, asynchronous=False, timeout=180):
        public_url(url, resolve=self.transport is None)
        try:
            with httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, url, headers={"Authorization": f"Bearer {key}",
                    **({"X-DashScope-Async": "enable"} if asynchronous else {})}, json=payload)
        except httpx.RequestError:
            raise DomainError("只读查询未完成，请稍后再查。" if method == "GET" else
                              "媒体请求未完整结束，费用可能已发生；保留任务，不会自动重试生成。", 502) from None
        if response.status_code not in (200, 201, 202):
            diagnostic = media_http_diagnostic(response)
            exc = DomainError(media_http_error(response, diagnostic), 502)
            exc.provider_diagnostic = diagnostic
            raise exc
        try:
            result = response.json()
            if not isinstance(result, dict) or result.get("error") or result.get("code"):
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise DomainError("媒体供应商结果无法识别；请核对任务与费用，不会自动重试。", 502) from None

    def generate(self, value, config, images, aspect, on_remote):
        prompt = value.prompt
        if value.audio and value.audio_direction.strip():
            prompt += "\n\n声音设计（时间相对本镜头开始，属于生成要求，效果需人工试听）：\n" + value.audio_direction.strip()
        if value.provider == "seedream":
            # Flash supports pixel sizes as well as 2K presets. Keep exact Brief
            # aspect ratio; all three sizes meet its 921600-pixel minimum.
            dimensions = {"16:9": "1536x864", "9:16": "864x1536", "1:1": "1152x1152"}
            body = {"model": config.seedream.model, "prompt": value.prompt, "size": dimensions[aspect],
                    "response_format": "b64_json", "stream": False, "output_format": "png", "watermark": True}
            if images:
                body["image"] = images[0] if len(images) == 1 else images
            result = self.request("POST", ARK_BASE + "/images/generations", config.seedream.api_key, body)
            try:
                data = result["data"]
                if len(data) != 1:
                    raise ValueError()
                content = base64.b64decode(data[0]["b64_json"], validate=True)
                if not content or len(content) > MAX_MEDIA:
                    raise ValueError()
                return {"bytes": content, "media_type": "image", "extension": ".png", "usage": self.usage(result)}
            except (KeyError, ValueError, TypeError):
                raise DomainError("生图返回不完整，费用需核对；不会重新生成。", 502) from None
        if value.provider == "cosyvoice":
            result = self.request("POST", ali_base(config.aliyun) + "/services/audio/tts/SpeechSynthesizer",
                config.aliyun.api_key, {"model": config.aliyun.tts_model, "input": {
                    "text": value.prompt, "voice": config.aliyun.voice, "format": "wav", "sample_rate": 24000}})
            output = result.get("output") or {}
            if output.get("finish_reason") != "stop" or not (output.get("audio") or {}).get("url"):
                raise DomainError("配音结果未完整结束；费用需核对，不会重新合成。", 502)
            on_remote(result.get("request_id"), output["audio"]["url"], self.usage(result))
            return {"url": output["audio"]["url"], "media_type": "audio", "extension": ".wav", "usage": self.usage(result)}
        if value.provider == "wan":
            body = {"model": config.aliyun.video_model, "input": {"prompt": prompt, "img_url": images[0]},
                    "parameters": {"resolution": value.resolution, "duration": value.duration,
                        "audio": value.audio, "prompt_extend": False, "shot_type": "single", "watermark": True}}
            if value.seed is not None:
                body["parameters"]["seed"] = value.seed
            if value.negative_prompt:
                body["input"]["negative_prompt"] = value.negative_prompt
            result = self.request("POST", ali_base(config.aliyun) + "/services/aigc/video-generation/video-synthesis",
                                  config.aliyun.api_key, body, asynchronous=True)
            remote = (result.get("output") or {}).get("task_id")
        elif config.seedance.protocol == "openrouter":
            self.check_openrouter(config, value, aspect)
            body = {"model": config.seedance.model, "prompt": prompt, "duration": value.duration,
                    "resolution": "720p", "aspect_ratio": aspect, "generate_audio": value.audio}
            if images:
                body["frame_images"] = [{"type": "image_url", "image_url": {"url": images[0]}, "frame_type": "first_frame"}]
            if value.seed is not None:
                body["seed"] = value.seed
            result = self.request("POST", OPENROUTER_BASE + "/videos", config.seedance.api_key, body)
            remote = result.get("id")
        else:
            content = [{"type": "text", "text": value.prompt}]
            if images:
                content.append({"type": "image_url", "image_url": {"url": images[0]}, "role": "first_frame"})
            body = {"model": config.seedance.model, "content": content, "duration": value.duration,
                    "resolution": "720p", "ratio": "adaptive" if images else aspect, "generate_audio": False, "watermark": True}
            if value.seed is not None:
                body["seed"] = value.seed
            result = self.request("POST", config.seedance.base_url.rstrip("/") + "/contents/generations/tasks",
                                  config.seedance.api_key, body)
            remote = result.get("id")
        self.remote_id(remote)
        on_remote(remote, None, {})
        return None

    @staticmethod
    def remote_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
            raise DomainError("未收到有效供应商任务ID；请核对费用，不会重新生成。", 502)

    @staticmethod
    def usage(result):
        return {k: v for k, v in (result.get("usage") or {}).items()
                if k in ("characters", "completion_tokens", "total_tokens", "image_count", "video_duration")
                and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0}

    def check_openrouter(self, config, value=None, aspect=None):
        require_openrouter(config)
        if not config.seedance.api_key:
            raise DomainError("请先保存OpenRouter Key", 422)
        key = self.request("GET", OPENROUTER_BASE + "/key", config.seedance.api_key, timeout=20)
        if not isinstance(key.get("data"), dict) or key["data"].get("disabled"):
            raise DomainError("OpenRouter Key未启用或鉴权结果不完整", 422)
        data = self.request("GET", OPENROUTER_BASE + "/videos/models", config.seedance.api_key, timeout=20)
        model = next((m for m in data.get("data", []) if isinstance(m, dict) and m.get("id") == config.seedance.model), None)
        if model is None:
            raise DomainError("OpenRouter目录中没有该模型，请核对完整模型名", 422)
        if value is not None:
            checks = [(value.duration, "supported_durations"), ("720p", "supported_resolutions"),
                      (aspect, "supported_aspect_ratios")]
            if any(item not in model.get(field, []) for item, field in checks):
                raise DomainError("OpenRouter模型不支持此次时长、分辨率或画幅；未发送生成请求", 422)
            if value.reference_ids and "first_frame" not in model.get("supported_frame_images", []):
                raise DomainError("此OpenRouter模型不支持首帧；未发送生成请求", 422)
            if value.seed is not None and not model.get("seed"):
                raise DomainError("此OpenRouter模型不支持seed；未发送生成请求", 422)
            if value.audio and model.get("generate_audio") is not True:
                raise DomainError("此OpenRouter模型目录未明确支持生成声音；未发送生成请求", 422)
        return {"authenticated": True, "model": config.seedance.model,
                **{k: model.get(k) for k in ("supported_durations", "supported_resolutions", "supported_aspect_ratios", "supported_frame_images", "pricing_skus", "generate_audio")},
                "supplier_currency": "USD", "message": "鉴权和模型目录查询通过；没有生成视频，未验证实际生成质量及收费。"}

    def query(self, provider, remote, config):
        self.remote_id(remote)
        if provider == "wan":
            result = self.request("GET", ali_base(config.aliyun) + "/tasks/" + quote(remote), config.aliyun.api_key)
            output = result.get("output") or {}
            status, url = output.get("task_status"), output.get("video_url")
        elif config.seedance.protocol == "openrouter":
            require_openrouter(config)
            result = self.request("GET", OPENROUTER_BASE + "/videos/" + quote(remote), config.seedance.api_key)
            status = result.get("status")
            if status == "completed":
                usage = self.usage(result)
                cost = (result.get("usage") or {}).get("cost")
                if isinstance(cost, (float, int)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                    usage["cost_usd"] = cost
                # Reconstruct our own authenticated endpoint; never forward keys to unsigned_urls/polling_url.
                return {"url": OPENROUTER_BASE + "/videos/" + quote(remote) + "/content?index=0",
                        "media_type": "video", "extension": ".mp4", "usage": usage}
            if status in ("pending", "in_progress"):
                return None
            raise DomainError("OpenRouter任务失败、取消、过期或状态未知；请核对原任务，不会重新生成", 502)
        else:
            result = self.request("GET", config.seedance.base_url.rstrip("/") + "/contents/generations/tasks/" + quote(remote), config.seedance.api_key)
            status, url = result.get("status"), (result.get("content") or {}).get("video_url")
        if status in ("SUCCEEDED", "succeeded") and isinstance(url, str):
            return {"url": url, "media_type": "video", "extension": ".mp4", "usage": self.usage(result)}
        if status not in ("PENDING", "RUNNING", "pending", "running", "queued", "submitted"):
            raise DomainError("供应商任务失败、取消或状态未知；请核对费用，不会重新生成。", 502)
        return None

    def download_openrouter(self, remote, config, path):
        require_openrouter(config)
        self.remote_id(remote)
        url = OPENROUTER_BASE + "/videos/" + quote(remote) + "/content?index=0"
        public_url(url, resolve=self.transport is None)
        try:
            with httpx.Client(transport=self.transport, timeout=120, follow_redirects=False, trust_env=False) as client:
                with client.stream("GET", url, headers={"Authorization": "Bearer " + config.seedance.api_key}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise DomainError("OpenRouter下载重定向缺少地址", 502)
                        # Redirects go through an independent client without Authorization.
                        self.download(location, path)
                        return
                    self.save_download(response, path)
        except httpx.RequestError:
            raise DomainError("OpenRouter下载中断；请取回原结果，不会重新生成", 502) from None

    @staticmethod
    def save_download(response, path):
        if response.status_code != 200:
            raise DomainError("素材下载失败；供应商生成可能已收费，请稍后取回原结果。", 502)
        size = 0
        with path.open("wb") as out:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_MEDIA:
                    raise DomainError("供应商素材超过100MB，未导入；请核对费用。", 413)
                out.write(chunk)
        if not size:
            raise DomainError("供应商返回空素材，请核对费用。", 502)

    def download(self, url, path):
        # Some official OSS links use http; upgrade only official Aliyun hosts.
        parsed = urlparse(url)
        if parsed.scheme == "http" and (parsed.hostname or "").endswith(".aliyuncs.com"):
            url = urlunparse(parsed._replace(scheme="https"))
        public_url(url, resolve=self.transport is None)
        try:
            # A separate unauthenticated client prevents sending supplier keys to a CDN.
            with httpx.Client(transport=self.transport, timeout=120, follow_redirects=False, trust_env=False) as client:
                with client.stream("GET", url) as response:
                    self.save_download(response, path)
        except httpx.RequestError:
            raise DomainError("素材下载中断；保留原结果，不会重新生成。", 502) from None


def register_media(app, store):
    settings = MediaSettings(store.root)
    adapter = MediaAdapter()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media")
    app.state.media_settings, app.state.media_adapter = settings, adapter
    app.state.media_executor = executor
    app.state.media_catalog = settings.catalog
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS media_jobs(id TEXT PRIMARY KEY,project_id TEXT,status TEXT,
            provider TEXT,config TEXT,request TEXT,input_refs TEXT,brief_revision INTEGER,remote_id TEXT,
            result TEXT,reservation REAL,actual_cost REAL,cost_evidence TEXT,error TEXT,created_at TEXT)""")
        db.execute("UPDATE media_jobs SET status='interrupted',error='进程中断；取回原结果或核账，不会自动重试生成。' WHERE status='running'")
        columns = {r["name"] for r in db.execute("PRAGMA table_info(media_jobs)")}
        for field in ("source_task_id", "source_chat_job_id", "source_action_id"):
            if field not in columns:
                db.execute(f"ALTER TABLE media_jobs ADD COLUMN {field} TEXT")

    def job(db, pid, jid):
        row = db.execute("SELECT * FROM media_jobs WHERE project_id=? AND id=?", (pid, jid)).fetchone()
        if not row:
            raise DomainError("媒体任务不存在", 404)
        value = dict(row)
        for key in ("config", "request", "input_refs", "result"):
            value[key] = json.loads(value[key])
        try:
            store.validate_refs(db, pid, value["input_refs"])
            value["stale"] = store.project(db, pid)["revision"] != value["brief_revision"]
        except DomainError:
            value["stale"] = True
        # Signed provider URLs are only stored for recovery, never exposed in snapshots.
        value["result"].pop("download_url", None)
        return value

    app.state.media_jobs = lambda db, pid: [job(db, pid, r["id"]) for r in db.execute(
        "SELECT id FROM media_jobs WHERE project_id=? ORDER BY created_at DESC", (pid,))]

    @app.get("/api/media-settings")
    def get_settings():
        return settings.status()

    @app.put("/api/media-settings")
    def save_settings(value: MediaConfig):
        return settings.save(value)

    @app.post("/api/media-settings/seedance/check")
    def check_seedance():
        config = settings.config()
        if config.seedance.protocol != "openrouter":
            raise DomainError("此连接测试用于OpenRouter，请先保存对应协议", 422)
        return adapter.check_openrouter(config)

    def prepare(db, pid, value):
        project = store.project(db, pid)
        refs = [r.model_dump() for r in value.input_refs]
        images = []
        for aid in value.reference_ids:
            asset = store.record(db, pid, "asset", aid)
            reference = {"kind": "asset", "id": aid, "version": asset["version"]}
            if reference not in refs:
                refs.append(reference)
            images.append(image_data_uri(store, pid, asset))
        store.validate_refs(db, pid, refs)
        return project, refs, images

    @app.post("/api/projects/{pid}/media-estimate")
    def quote_media(pid: str, value: MediaRequest):
        config = settings.config()
        quotation = estimate(value, config)
        with store.connect() as db:
            project, refs, _ = prepare(db, pid, value)
        return {**quotation, "config_version": config_version(config), "brief_revision": project["revision"], "input_refs": refs}

    def remember_remote(pid, jid, remote, url, usage):
        with store.transaction() as db:
            previous = json.loads(db.execute("SELECT result FROM media_jobs WHERE id=? AND project_id=?", (jid, pid)).fetchone()[0])
            db.execute("UPDATE media_jobs SET remote_id=COALESCE(?,remote_id),result=? WHERE id=? AND project_id=?",
                (remote, dump({**previous, **({"download_url": url} if url else {}), "usage": usage}), jid, pid))

    def finish(pid, jid, output, config):
        with store.connect() as db:
            original = job(db, pid, jid)
        folder = store.root / "media" / pid
        folder.mkdir(parents=True, exist_ok=True)
        aid = uid("asset")
        path = folder / (aid + output["extension"])
        try:
            if "bytes" in output:
                path.write_bytes(output["bytes"])
            elif original["provider"] == "seedance" and original["config"]["seedance"]["protocol"] == "openrouter":
                adapter.download_openrouter(original["remote_id"], config, path)
            else:
                adapter.download(output["url"], path)
            if output["media_type"] == "image":
                with Image.open(path) as im:
                    im.verify()
            else:
                probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path),
                    "-t", "0.05", "-f", "null", "-"], capture_output=True, timeout=25,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if probe.returncode:
                    raise DomainError("生成文件无法解码；保留供应商任务，请核对费用。", 502)
            audio_metadata = ({"audio_requested": bool(original["request"].get("audio")),
                               "audio_direction": original["request"].get("audio_direction", ""),
                               "has_audio_stream": has_audio_stream(path)} if output["media_type"] == "video" else {})
            with store.transaction() as db:
                current = job(db, pid, jid)
                metadata = {"name": f"{original['provider']} · {original['request']['prompt'][:30]}",
                    "path": str(path), "url": f"/media/{pid}/{path.name}", "size": path.stat().st_size,
                    "media_type": output["media_type"], "source": "api_generation", "license": "待核实",
                    "provider": original["provider"], "model": original["result"]["model"], "media_job_id": jid,
                    "remote_id": current["remote_id"], "usage": output["usage"], "human_accepted": False, **audio_metadata}
                # Historical input versions remain provenance even if edited during the external job.
                db.execute("INSERT INTO records(project_id,kind,id,version,data,input_refs,stale,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (pid, "asset", aid, 1, dump(metadata), dump(original["input_refs"]), int(current["stale"]), now()))
                raw = json.loads(db.execute("SELECT result FROM media_jobs WHERE id=?", (jid,)).fetchone()[0])
                raw.pop("download_url", None)
                db.execute("UPDATE media_jobs SET status='completed',result=?,error=NULL WHERE id=?",
                    (dump({**raw, "asset_id": aid, "usage": output["usage"]}), jid))
                store.event(db, pid, "media.completed", {"job_id": jid, "asset_id": aid, "human_accepted": False})
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def worker(pid, jid, value, config, images, aspect, existing=None):
        try:
            output = existing
            if output is None:
                output = adapter.generate(value, config, images, aspect, lambda r, u, usage: remember_remote(pid, jid, r, u, usage))
            if output is None:
                with store.connect() as db:
                    remote = job(db, pid, jid)["remote_id"]
                deadline = time.monotonic() + 600
                while output is None:
                    output = adapter.query(value.provider, remote, config)
                    if output is None:
                        if time.monotonic() >= deadline:
                            raise DomainError("供应商仍在生成；请稍后取回原结果，不会重复创建任务。", 502)
                        time.sleep(30 if value.provider == "seedance" and config.seedance.protocol == "openrouter" else 15)
                remember_remote(pid, jid, remote, output["url"], output["usage"])
            finish(pid, jid, output, config)
        except Exception as exc:
            message = exc.message if isinstance(exc, DomainError) else "媒体处理未完成；请核对原任务和费用，不会自动重试生成。"
            # Never expose provider bodies or exception strings containing credentials/URLs.
            with store.transaction() as db:
                db.execute("UPDATE media_jobs SET status='interrupted',error=? WHERE id=? AND project_id=?", (message, jid, pid))
                diagnostic = getattr(exc, "provider_diagnostic", None)
                if diagnostic is not None:
                    previous = json.loads(db.execute("SELECT result FROM media_jobs WHERE id=? AND project_id=?", (jid, pid)).fetchone()[0])
                    db.execute("UPDATE media_jobs SET result=? WHERE id=? AND project_id=?", (dump({**previous, "provider_error": diagnostic}), jid, pid))
                store.event(db, pid, "media.interrupted", {"job_id": jid, "message": message})

    def generate_media(pid, value, request_id, origin=None, guard=None):
        launch = None
        with store.transaction() as db:
            def operation():
                nonlocal launch
                if guard is not None:
                    guard(db)
                config = settings.config()
                if value.expected_config_version != config_version(config):
                    raise DomainError("供应商配置已改变，请重新估算费用再确认")
                ready_name = {"wan": "aliyun", "cosyvoice": "aliyun"}.get(value.provider, value.provider)
                if not settings.status(config)[ready_name]["ready"]:
                    raise DomainError("请先在运行设置填写该供应商Key和必需配置", 422)
                quotation = estimate(value, config)
                if value.max_cost + 1e-8 < quotation["estimated_cost"]:
                    raise DomainError("预留金额小于本次估算费用", 422)
                project, refs, images = prepare(db, pid, value)
                if value.expected_brief_revision != project["revision"]:
                    raise DomainError("项目Brief已修改，请重新估算并确认本次生成")
                charges = [dict(r) for r in db.execute("SELECT reservation,actual_cost FROM tasks WHERE project_id=?", (pid,))]
                for table in ("chat_jobs", "media_jobs"):
                    charges += [dict(r) for r in db.execute(f"SELECT reservation,actual_cost FROM {table} WHERE project_id=?", (pid,))]
                used = sum(c["reservation"] if c["actual_cost"] is None else c["actual_cost"] for c in charges)
                if used + value.max_cost > project["budget"] + 1e-8:
                    raise DomainError("项目剩余预算不足，请先核对已执行费用或调整预算")
                if db.execute("SELECT COUNT(*) FROM media_jobs WHERE status='running'").fetchone()[0] >= 2:
                    raise DomainError("已有两个媒体任务执行中，请等一个完成后再生成")
                jid = uid("media")
                data = value.model_dump(exclude={"confirmed", "max_cost", "expected_config_version", "expected_brief_revision"})
                public_config = settings.status(config)
                public_config.pop("message", None)
                source = origin or {}
                db.execute("""INSERT INTO media_jobs(id,project_id,status,provider,config,request,input_refs,brief_revision,
                    remote_id,result,reservation,actual_cost,cost_evidence,error,created_at,source_task_id,source_chat_job_id,source_action_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (jid, pid, "running", value.provider,
                    dump(public_config), dump(data), dump(refs), project["revision"], None, dump(quotation), value.max_cost, None, None, None, now(),
                    source.get("task_id"), source.get("chat_job_id"), source.get("action_id")))
                store.event(db, pid, "media.started", {"job_id": jid, "provider": value.provider, "reserved": value.max_cost})
                launch = (pid, jid, MediaRequest(**data), config, images, project["aspect"])
                return job(db, pid, jid)
            result = store.idempotent(db, pid, "media.generate", request_id, value.model_dump(), operation)
        if launch:
            executor.submit(worker, *launch)
        return result

    app.state.generate_media = generate_media
    app.state.quote_media = quote_media
    app.state.get_media_job = job

    @app.post("/api/projects/{pid}/media-jobs")
    def generate(pid: str, value: MediaGenerate, x_request_id: str = Header(default="")):
        return generate_media(pid, value, x_request_id)

    @app.get("/api/projects/{pid}/media-jobs")
    def list_jobs(pid: str):
        with store.connect() as db:
            store.project(db, pid)
            return app.state.media_jobs(db, pid)

    @app.post("/api/projects/{pid}/media-jobs/{jid}/retrieve")
    def retrieve(pid: str, jid: str):
        config = settings.config()
        with store.transaction() as db:
            original = job(db, pid, jid)
            if original["status"] == "completed":
                return original
            if original["status"] == "running":
                raise DomainError("任务正在处理，请等待")
            raw = json.loads(db.execute("SELECT result FROM media_jobs WHERE id=?", (jid,)).fetchone()[0])
            value = MediaRequest(**original["request"])
            old = original["config"]
            current = settings.status(config)
            section = "seedance" if value.provider == "seedance" else "aliyun"
            fields = ("base_url", "model", "protocol") if section == "seedance" else ("workspace_id", "video_model", "tts_model")
            if any(old[section].get(k) != current[section].get(k) for k in fields):
                raise DomainError("供应商配置已改变，请恢复原任务的地址和模型后取回结果")
            if not current[section]["has_key"]:
                raise DomainError("请先配置原供应商Key", 422)
            output = None
            if raw.get("download_url"):
                output = {"url": raw["download_url"], "media_type": "audio" if value.provider == "cosyvoice" else "video",
                          "extension": ".wav" if value.provider == "cosyvoice" else ".mp4", "usage": raw.get("usage", {})}
            elif value.provider not in ("wan", "seedance") or not original["remote_id"]:
                raise DomainError("没有可取回的供应商结果；请人工核账，本操作不会重新生成")
            if db.execute("SELECT COUNT(*) FROM media_jobs WHERE status='running'").fetchone()[0] >= 2:
                raise DomainError("已有两个媒体任务执行中")
            db.execute("UPDATE media_jobs SET status='running',error=NULL WHERE id=?", (jid,))
        # Resume GET/download only. Never repeat a generation POST.
        def resume():
            try:
                found = output or adapter.query(value.provider, original["remote_id"], config)
                if found is None:
                    raise DomainError("原任务仍在生成，请稍后再次取回。")
                if found.get("url"):
                    remember_remote(pid, jid, None, found["url"], found["usage"])
                finish(pid, jid, found, config)
            except Exception as exc:
                message = exc.message if isinstance(exc, DomainError) else "取回素材失败；不会重新生成。"
                with store.transaction() as db:
                    db.execute("UPDATE media_jobs SET status='interrupted',error=? WHERE id=?", (message, jid))
        executor.submit(resume)
        return {"id": jid, "status": "running"}

    @app.post("/api/projects/{pid}/media-jobs/{jid}/reconcile")
    def reconcile(pid: str, jid: str, value: Reconcile):
        with store.transaction() as db:
            original = job(db, pid, jid)
            if original["status"] == "running":
                raise DomainError("媒体任务还在执行，请结束后核账")
            db.execute("UPDATE media_jobs SET actual_cost=?,cost_evidence=? WHERE id=? AND project_id=?",
                (value.actual_cost, value.evidence, jid, pid))
            store.event(db, pid, "media.reconciled", {"job_id": jid, "actual_cost": value.actual_cost})
            return job(db, pid, jid)

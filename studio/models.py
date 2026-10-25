from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

ROLES = ["director", "audience", "writer", "assets", "storyboard", "post"]
KINDS = {"hypothesis", "reference", "experiment", "asset", "artifact", "shot", "decision", "lesson", "review", "bible"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    brief: str = Field(default="", max_length=20000)
    audience: str = Field(default="", max_length=2000)
    promise: str = Field(default="", max_length=2000)
    action: str = Field(default="关注", max_length=100)
    followup: str = Field(default="", max_length=2000)
    duration: float = Field(default=30, gt=0, le=300)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    budget: float = Field(default=0, ge=0, le=1000000)
    sample: bool = False


class ProjectEdit(ProjectInput):
    expected_revision: int = Field(ge=1)


class Ref(StrictModel):
    kind: str
    id: str
    version: int = Field(ge=1)


class RecordInput(StrictModel):
    id: str | None = None
    expected_version: int = Field(default=0, ge=0)
    data: dict[str, Any]
    input_refs: list[Ref] = Field(default_factory=list)


class TaskInput(StrictModel):
    role: Literal["director", "audience", "writer", "assets", "storyboard", "post"]
    goal: str = Field(min_length=1, max_length=10000)
    dependencies: list[str] = Field(default_factory=list)
    input_refs: list[Ref] = Field(default_factory=list)


class StartTask(StrictModel):
    max_cost: float = Field(gt=0, le=1000000)


class ChatInput(StartTask):
    message: str = Field(min_length=1, max_length=20000)
    mode: Literal['discussion','finalize'] = 'discussion'
    media_budget: float = Field(default=10, ge=0, le=1000000, allow_inf_nan=False)


class FinalizeChat(StrictModel):
    message_id: str


class ConfirmStage(StrictModel):
    confirmed: Literal[True]
    expected_brief_revision: int = Field(ge=1)
    message_id: str | None = None
    artifact_version: int = Field(default=0, ge=0)
    merge_audience: bool = False
    acknowledged_uncertainties: bool = False
    start_next: bool = True
    max_cost: float = Field(default=1, gt=0, le=1000000)


class ReopenStage(StrictModel):
    expected_brief_revision: int = Field(ge=1)
    artifact_version: int = Field(default=0, ge=0)


class ProposedRecord(StrictModel):
    kind: Literal["hypothesis", "reference", "experiment", "shot", "lesson", "bible"]
    data: dict[str, Any]


class RoleResult(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=5000)
    body: str = Field(min_length=1, max_length=100000)
    uncertainties: list[str] = Field(default_factory=list)
    proposed_records: list[ProposedRecord] = Field(default_factory=list)


class Check(StrictModel):
    criterion: str = Field(min_length=1, max_length=2000)
    status: Literal["passed", "failed", "unknown"]
    evidence_refs: list[Ref] = Field(min_length=1)
    note: str = Field(default="", max_length=5000)


class ReviewInput(StrictModel):
    outcome: Literal["accepted", "changes_requested"]
    note: str = Field(default="", max_length=10000)
    checks: list[Check] = Field(min_length=1)
    acknowledged_uncertainties: bool = False


class Reconcile(StrictModel):
    actual_cost: float = Field(ge=0, le=1000000)
    evidence: str = Field(min_length=1, max_length=2000)


class RuntimeConfig(StrictModel):
    enabled: bool = False
    provider: str = "deepseek-official"
    model: str = "deepseek-v4-flash"
    base_url: str = ""
    api_key: str | None = None
    clear_key: bool = False
    request_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    max_output_tokens: int = Field(default=16384, ge=1024, le=65536)
    reasoning_effort: Literal["low", "high"] = "low"


class ExportInput(StrictModel):
    shot_ids: list[str] = Field(min_length=1, max_length=100)
    name: str = Field(default="动态分镜", max_length=100)
    preserve_source_audio: bool = True
    source_gain: float = Field(default=1, ge=0, le=2, allow_inf_nan=False)
    narration_asset_id: str | None = Field(default=None, max_length=200)
    narration_gain: float = Field(default=1, ge=0, le=2, allow_inf_nan=False)
    narration_start_seconds: float = Field(default=0, ge=0, le=300, allow_inf_nan=False)


class GeminiConfig(StrictModel):
    # No enabled flag or user-controlled destination for this credential.
    model: str = Field(default="gemini-3.8-flash", pattern=r"^gemini-[A-Za-z0-9.-]+$", max_length=100)
    api_key: str | None = Field(default=None, max_length=1000, repr=False)
    clear_key: bool = False
    request_timeout_seconds: int = Field(default=120, ge=30, le=300)
    max_output_tokens: int = Field(default=3072, ge=512, le=8192)
    fps: float = Field(default=1, ge=0.2, le=5)


class GeminiAnalysisInput(StrictModel):
    asset_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=10000)


class SubmitInput(RoleResult):
    expected_version: int = Field(default=0, ge=0)
    final_asset_id: str | None = None


class MediaCredential(StrictModel):
    api_key: str | None = Field(default=None, max_length=1000, repr=False)
    clear_key: bool = False


class SeedreamConfig(MediaCredential):
    model: Literal["doubao-seedream-5-0-flash-260915"] = "doubao-seedream-5-0-flash-260915"


class AliMediaConfig(MediaCredential):
    workspace_id: str = Field(default="", pattern=r"^[A-Za-z0-9-]{0,100}$")
    video_model: Literal["wan2.6-i2v-flash"] = "wan2.6-i2v-flash"
    tts_model: Literal["cosyvoice-v3-plus", "cosyvoice-v3-flash", "cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash"] = "cosyvoice-v3-plus"
    voice: str = Field(default="longanyang", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


class SeedanceRelayConfig(MediaCredential):
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=150, pattern=r"^[A-Za-z0-9._/-]*$")
    protocol: Literal["unconfigured", "ark", "openrouter"] = "unconfigured"
    price_per_second: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    audio_price_per_second: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)


class MediaConfig(StrictModel):
    seedream: SeedreamConfig = Field(default_factory=SeedreamConfig)
    aliyun: AliMediaConfig = Field(default_factory=AliMediaConfig)
    seedance: SeedanceRelayConfig = Field(default_factory=SeedanceRelayConfig)


class MediaRequest(StrictModel):
    provider: Literal["seedream", "wan", "cosyvoice", "seedance"]
    prompt: str = Field(min_length=1, max_length=10000)
    reference_ids: list[str] = Field(default_factory=list, max_length=10)
    input_refs: list[Ref] = Field(default_factory=list, max_length=100)
    duration: int = Field(default=5, ge=2, le=15)
    resolution: Literal["720P", "1080P"] = "720P"
    audio: bool = False
    audio_direction: str = Field(default="", max_length=3000)
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    negative_prompt: str = Field(default="", max_length=500)


class MediaGenerate(MediaRequest):
    confirmed: Literal[True]
    max_cost: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    expected_config_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_brief_revision: int = Field(ge=1)


class StoryboardMediaRequest(MediaRequest):
    provider: Literal["seedream", "wan", "seedance"]


class GenerateMediaAction(StrictModel):
    tool: Literal["generate_media"]
    action_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    request: StoryboardMediaRequest


class GetMediaAction(StrictModel):
    tool: Literal["get_media_result"]
    action_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    job_id: str = Field(min_length=1, max_length=200)


class BatchImageItem(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    request: StoryboardMediaRequest


class GenerateImageBatchAction(StrictModel):
    tool: Literal["generate_image_batch"]
    action_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=120)
    items: list[BatchImageItem] = Field(min_length=1, max_length=50)


class StoryboardReply(RoleResult):
    # Separate contract: existing RoleResult schema and accepted task hashes stay stable.
    media_actions: list[GenerateMediaAction | GetMediaAction | GenerateImageBatchAction] = Field(default_factory=list, max_length=2)


class AssetEdit(StrictModel):
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1,max_length=200)
    license: str = Field(min_length=1,max_length=5000)
    source_note: str = Field(default="",max_length=5000)


class ResolveInput(StrictModel):
    note: str = Field(min_length=1, max_length=3000)

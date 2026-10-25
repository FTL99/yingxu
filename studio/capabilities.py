"""Actual application capabilities, independent of a role's suggested tasks."""


def capabilities():
    # No writable setting: enabling this requires a real media API integration.
    return {
        "role_revision": {"enabled": True, "sync_requires_confirmation": True,
            "scope": "可返回角色讨论修改，确认后同步新版设定和变更说明；相关旧成果待更新，保留媒体，不自动重新生成。"},
        "image_replacement": {
            "enabled": True, "formats": ["png","jpg","jpeg","webp"], "max_size_mb": 10,
            "retain_history": True, "scope": "用户在图片预览或素材区上传替换，保留原素材ID与历史版本；不调用模型，关联成果需复查。",
        },
        "storyboard_media_tools": {
            "enabled": True, "role": "storyboard", "mode": "discussion",
            "tools": ["generate_media", "generate_image_batch", "get_media_result"],
            "default_turn_reservation": 10, "max_actions": 2, "batch_max_images": 50,
            "batch_execution": "sequential", "batch_failure_policy": "pause_without_retry",
            "scope": "在分镜讨论中使用已配置生图/视频接口，结果直接在对话展示；每轮素材额度与项目预算共同约束。",
            "transport": "host-executed structured media_actions",
            "media_understanding": False,
        },
        "local_audio_export": {
            "preserve_source_audio": True, "narration_tracks": 1,
            "volume_control": True, "narration_start_offset": True,
            "audio_understanding": False, "stem_separation": False,
            "scope": "本地动态分镜保留视频首条音轨，可叠加一条已有音频；需人工试听。",
        },
        "audience_mode": "strategy_only",
        "audience_video_validation": {
            "enabled": False,
            "label": "观众视频校验已禁用",
            "reason": "Gemini API 已封装但保持禁用；文字角色仍只接收文字与素材元数据。",
            "provider": "google-gemini",
            "adapter_configured": True,
            "scope": "保留受众定位、创意和测试方案讨论；模型推测不能作为真实观众反馈。",
        },
    }


CAPABILITY_RULES = """
能力边界补充：观众视频校验已禁用，audience 角色仅做文字策略与创意讨论。
当前没有视频/图片/音频理解工具；素材文件名、描述与元数据不等于观看过素材。
不得宣称已观看视频、完成观众校验或已证明观众会关注/转化。
可以设计测试方案，但未执行的测试与模型判断必须标为未验证。
上下文media_providers是工作台媒体供应商目录。只有分镜讨论且media_authorization.enabled=true，才可通过media_actions请求工作台生成并展示图片/视频；工具规则见该角色本轮合同。
其他角色及未授权轮次仅规划参数，由用户在分镜与素材中确认调用。未配置的供应商明确标为待配置。
不得因为tools为空就忽略media_providers；仅填写已封装支持的参数，生成不等于验收或观看。
真实人工反馈可以引用来源。G0只是人类验收策略方向，不代表观众行为已验证。
"""

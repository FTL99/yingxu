"""Offline animatic renderer. Only registered local media IDs are accepted."""
import json
import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .store import DomainError

FONT = Path("C:/Windows/Fonts/msyh.ttc")


def has_audio_stream(path):
    """Probe container streams locally; presence is not a judgement of sound quality."""
    probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                           capture_output=True, timeout=25,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import re
    return bool(re.search(r"Stream #\d+:\d+[^\n]*: Audio:", probe.stderr.decode("utf-8", errors="replace")))


def render_animatic(folder: Path, project, shots, assets, size=None, audio_options=None):
    from .models import ExportInput
    options = ExportInput(shot_ids=[s["id"] for s in shots], **(audio_options or {}))
    folder.mkdir(parents=True, exist_ok=True)
    width, height = size or {"9:16": (720,1280), "16:9": (1280,720), "1:1": (720,720)}[project["aspect"]]
    total = sum(float(s["data"].get("duration", 0)) for s in shots)
    if not 0 < total <= 300 or any(not 0 < float(s["data"].get("duration", 0)) <= 60 for s in shots):
        raise DomainError("镜头须为 0–60 秒，总片长须为 0–300 秒", 422)
    binary = imageio_ffmpeg.get_ffmpeg_exe()
    narration = assets.get(options.narration_asset_id) if options.narration_asset_id else None
    if options.narration_asset_id and (not narration or narration.get("media_type") != "audio"):
        raise DomainError("旁白须为本项目已导入的音频素材", 422)
    if narration and options.narration_start_seconds >= total:
        raise DomainError("旁白开始位置须小于导出总时长", 422)
    if narration and not has_audio_stream(narration["path"]):
        raise DomainError("旁白素材没有可用音频轨", 422)
    manifest, subtitles, elapsed = [], [], 0.0

    def stamp(seconds):
        ms = round(seconds * 1000)
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"

    for i, shot in enumerate(shots):
        d = shot["data"]
        duration = float(d["duration"])
        asset = assets.get(d.get("asset_id"))
        if d.get("asset_id") and not asset:
            raise DomainError("镜头引用的素材不存在", 422)
        font = ImageFont.truetype(str(FONT), max(14, width//25)) if FONT.exists() else ImageFont.load_default()
        small = ImageFont.truetype(str(FONT), max(12, width//36)) if FONT.exists() else font
        card = Image.new("RGB", (width,height), "#142724")
        draw = ImageDraw.Draw(card)
        if not asset:
            draw.rectangle((width*.1,height*.26,width*.9,height*.67), outline="#385449", width=2)
            draw.text((width*.12,height*.3), f"SHOT {i+1:02d}", font=font, fill="#c9f4ad")
            text = str(d.get("description", "待补充分镜描述"))
            max_chars = max(6, width//(font.size+2)-3)
            lines = [text[p:p+max_chars] for p in range(0,min(len(text),max_chars*5),max_chars)]
            draw.multiline_text((width*.12,height*.37), "\n".join(lines), font=font, fill="#f2f0df", spacing=10)
            draw.text((width*.1,height*.7), "动态分镜规划卡 · 未生成最终镜头", font=small, fill="#9fada6")
        card_path = folder / f"card-{i:03}.png"
        card.save(card_path)
        source = Path(asset["path"]) if asset else card_path
        is_video = asset and asset["media_type"] == "video"
        if asset and asset["media_type"] not in ("image", "video"):
            raise DomainError("画面素材须为图片或视频", 422)
        # PCM intermediates avoid accumulating AAC encoder padding at each cut.
        clip = folder / f"clip-{i:03}.mkv"
        # Hold the last video frame instead of replaying impact sounds from the start.
        input_args = [] if is_video else ["-loop", "1"]
        source_has_audio = bool(is_video and has_audio_stream(source))
        use_audio = source_has_audio and options.preserve_source_audio
        args = [binary,"-y","-hide_banner","-loglevel","error",*input_args,"-i",str(source),"-f","lavfi","-i","anullsrc=r=48000:cl=stereo",
                "-map","0:v:0","-map","0:a:0" if use_audio else "1:a:0",
                "-t",str(duration),"-vf",f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x142724,setsar=1,tpad=stop_mode=clone:stop_duration={duration}",
                "-af",f"aresample=48000,aformat=channel_layouts=stereo,volume={options.source_gain},apad,atrim=duration={duration}",
                "-r","24","-c:v","libx264","-preset","fast","-crf","23","-pix_fmt","yuv420p","-c:a","pcm_s16le","-ar","48000","-ac","2",str(clip)]
        proc = subprocess.run(args, capture_output=True, timeout=180, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if proc.returncode:
            raise DomainError("素材解码或动态分镜渲染失败。请检查媒体格式。",422)
        subtitle = str(d.get("subtitle", ""))
        if subtitle:
            subtitles.append(f"{len(subtitles)+1}\n{stamp(elapsed)} --> {stamp(elapsed+duration)}\n{subtitle}\n")
        manifest.append({"shot_id":shot["id"],"version":shot["version"],"duration":duration,"source":"uploaded_media" if asset else "planning_card","asset_id":d.get("asset_id"),
                         "source_has_audio":source_has_audio,"source_audio_used":bool(use_audio),"audio_direction":d.get("audio_direction", "")})
        elapsed += duration
    listing = folder / "concat.txt"
    listing.write_text("\n".join(f"file 'clip-{i:03}.mkv'" for i in range(len(shots))),encoding="utf-8")
    output = folder / "animatic.mp4"
    joined = folder / "joined.mp4" if narration else output
    proc = subprocess.run([binary,"-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(listing),"-c:v","copy","-c:a","aac","-t",str(total),"-movflags","+faststart",str(joined)],capture_output=True,timeout=90,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if proc.returncode or not joined.exists():
        raise DomainError("动态分镜拼接失败",422)
    if narration:
        delay = round(options.narration_start_seconds * 1000)
        filters = (f"[1:a:0]aresample=48000,aformat=channel_layouts=stereo,volume={options.narration_gain},"
                   f"adelay={delay}:all=1,apad,atrim=duration={total}[voice];"
                   "[0:a:0][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                   "alimiter=limit=0.95:level=false:latency=true[mix]")
        proc = subprocess.run([binary,"-y","-hide_banner","-loglevel","error","-i",str(joined),"-i",str(narration["path"]),
                               "-filter_complex",filters,"-map","0:v:0","-map","[mix]","-c:v","copy","-c:a","aac",
                               "-ar","48000","-ac","2","-t",str(total),"-movflags","+faststart",str(output)],
                              capture_output=True,timeout=90,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if proc.returncode or not output.exists():
            raise DomainError("旁白混音失败，请检查音频素材",422)
    (folder/"subtitles.srt").write_text("\n".join(subtitles),encoding="utf-8")
    audio = {**options.model_dump(exclude={"shot_ids", "name"}),
             "narration_policy":"按开始位置放入，超出总时长部分截断；不足补静音",
             "source_policy":"保留首条原声音轨；视频不足保持末帧，声音结束后补静音",
             "quality_review":"仅本地解码和音轨检查；声画同步、内容及听感须人工试听"}
    (folder/"manifest.json").write_text(json.dumps({"type":"animatic","duration":elapsed,"shots":manifest,"audio":audio,"subtitles":"独立 SRT，未烧录","final_movie":False},ensure_ascii=False,indent=2),encoding="utf-8")
    # Verify the actual container can be read, without relying on a success string.
    reader = imageio_ffmpeg.read_frames(str(output))
    try:
        metadata = next(reader)
        next(reader)
    finally:
        reader.close()
    if not math.isclose(metadata["duration"],elapsed,abs_tol=.3):
        raise DomainError("导出时长与镜头计划不一致",422)
    return {"duration":elapsed,"width":width,"height":height,"video":str(output),"manifest":str(folder/"manifest.json"),"subtitles":str(folder/"subtitles.srt")}

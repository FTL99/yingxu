"""Isolated UI QA. Scripted replies, fake keys and mock provider; no paid API."""
import base64
import io
import json
import subprocess
import uuid
from pathlib import Path

import httpx
import imageio_ffmpeg
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from studio.app import create_app
from studio.models import StoryboardReply
from studio.store import Store

root=Path(__file__).resolve().parents[1]/"data"/("qa-storyboard-"+uuid.uuid4().hex[:8])
root.mkdir(parents=True)
frame=Image.new("RGB",(640,360),"#142724")
draw=ImageDraw.Draw(frame)
draw.rectangle((40,40,600,320),outline="#c9f4ad",width=3)
draw.text((65,80),"QA ONLY / MOCK IMAGE",fill="#c9f4ad",font_size=30)
draw.text((65,145),"Generated result preview test",fill="white",font_size=24)
draw.text((65,235),"No paid media request",fill="white",font_size=20)
image=io.BytesIO();frame.save(image,format="PNG")
clip=root/"fixture.mp4"
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),"-v","error","-f","lavfi","-i","color=c=0x142724:s=320x180:r=24",
                "-f","lavfi","-i","sine=frequency=440:sample_rate=48000","-t","1","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(clip)],check=True,capture_output=True)

class Runner:
    def status(self): return {"ready":True}
    def run(self,*args):
        latest=args[-1]["conversation"][-1]["content"]
        if "队列" in latest or "批量" in latest:
            return StoryboardReply(title="批量分镜测试",summary="六张图片按队列生成",body="六张图片已安排为一批，工作台会逐张生成并展示总览。\n\n这是隔离模拟测试，不调用付费供应商。",
                media_actions=[{"tool":"generate_image_batch","action_id":"overview","title":"六镜头图片总览 · 隔离模拟测试",
                    "items":[{"name":name,"request":{"provider":"seedream","prompt":f"QA mock frame {i+1}: {name}"}} for i,name in enumerate(["开场：枪响","困境：围杀","转折：拔枪","点题：一句","证据：历史","高潮：反攻"])]}])
        video="视频" in latest
        request={"provider":"seedance","prompt":"界面测试视频，使用本地预设片段","duration":4,"audio":True,"audio_direction":"测试音轨"} if video else {"provider":"seedream","prompt":"界面测试样张，使用本地预设图片"}
        return StoryboardReply(title="分镜生成预览测试",summary="模拟接口，无真实模型调用",body="我先生成一个小样供你查看，结果会显示在这条回复下方。\n\n这是隔离的界面测试，画面和回复均为本地预设，没有付费调用。",
                               media_actions=[{"tool":"generate_media","action_id":"video" if video else "frame","request":request}])

app=create_app(root,Runner())
@app.middleware("http")
async def qa_origin(request,call_next):
    if request.headers.get("origin")=="http://127.0.0.1:8812":
        request.scope["headers"]=[(name,b"http://127.0.0.1:8811" if name==b"origin" else value) for name,value in request.scope["headers"]]
    return await call_next(request)

def respond(request):
    if request.url.host=="ark.cn-beijing.volces.com":
        data=json.loads(request.content)
        variant=frame.copy()
        ImageDraw.Draw(variant).text((65,290),data["prompt"].split(":")[0],fill="#c9f4ad",font_size=22)
        encoded=io.BytesIO();variant.save(encoded,format="PNG")
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(encoded.getvalue()).decode()}]})
    assert request.url.host=="openrouter.ai"
    if request.url.path.endswith("/key"):
        return httpx.Response(200,json={"data":{"disabled":False}})
    if request.url.path.endswith("/models"):
        return httpx.Response(200,json={"data":[{"id":"bytedance/seedance-2.0","generate_audio":True,"supported_durations":[4],"supported_resolutions":["720p"],"supported_aspect_ratios":["16:9"]}]})
    if request.method=="POST": return httpx.Response(202,json={"id":"mock-video-"+uuid.uuid4().hex[:8]})
    if request.url.path.endswith("/content"): return httpx.Response(200,content=clip.read_bytes())
    return httpx.Response(200,json={"status":"completed","usage":{"cost":.01}})
app.state.media_adapter.transport=httpx.MockTransport(respond)
client=TestClient(app)
client.headers["X-Studio-Token"]=client.get("/api/health").json()["token"]
client.put("/api/media-settings",json={"seedream":{"api_key":"qa-fake-key"},"seedance":{"api_key":"qa-fake-key","base_url":"https://openrouter.ai/api/v1","model":"bytedance/seedance-2.0","protocol":"openrouter","price_per_second":1,"audio_price_per_second":1.2}})
pid=client.post("/api/projects",json={"name":"分镜直接生成 · 隔离模拟测试","budget":100,"aspect":"16:9"},headers={"X-Request-ID":"project"}).json()["id"]
store=Store(root)
with store.transaction() as db:
    store.save_record(db,pid,"decision",{"stage":"G1","allow":True,"note":"isolated QA fixture"})
client.post(f"/api/projects/{pid}/tasks",json={"role":"storyboard","goal":"展示生成与结果预览"},headers={"X-Request-ID":"task"})
client.close()

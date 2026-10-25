"""Visible model output only. Never expose reasoning blocks or request headers."""
import json
from pathlib import Path

from .models import RoleResult


def partial_fields(text):
    """Read a JSON object's string prefixes without completing or approving it."""
    decoder=json.JSONDecoder()
    text=text.lstrip()
    if not text.startswith("{"):
        return {}
    at=1
    fields={}
    while at<len(text):
        while at<len(text) and text[at] in " \r\n\t,":
            at+=1
        try:
            key,at=decoder.raw_decode(text,at)
            if not isinstance(key,str):
                break
            while at<len(text) and text[at].isspace(): at+=1
            if text[at]!=":": break
            at+=1
            while at<len(text) and text[at].isspace(): at+=1
            start=at
            try:
                value,at=decoder.raw_decode(text,start)
                if key in ("title","summary","body") and isinstance(value,str):
                    fields[key]=value
            except json.JSONDecodeError:
                if key not in ("title","summary","body") or text[start]!='"':
                    break
                # Preserve a visible string prefix, dropping only incomplete
                # escape sequences. No invented suffix, fields or JSON repair.
                chars=[]
                at=start+1
                while at<len(text):
                    char=text[at]
                    if char=='"': break
                    if char!='\\':
                        chars.append(char);at+=1;continue
                    if at+1>=len(text): break
                    length=6 if text[at+1]=='u' else 2
                    chunk=text[at:at+length]
                    if len(chunk)!=length: break
                    try:
                        chars.append(json.loads('"'+chunk+'"'))
                    except json.JSONDecodeError:
                        break
                    at+=length
                fields[key]=''.join(chars)
                break
        except (json.JSONDecodeError,IndexError):
            break
    return fields


def output_view(text,finish_reason,usage=None):
    try:
        structured=RoleResult.model_validate_json(text).model_dump()
    except Exception:
        structured=None
    fields=structured or partial_fields(text)
    warnings=[]
    if finish_reason=="max-tokens":
        warnings.append("回答达到输出上限，尚未完整结束；这份草稿不能直接验收。")
    elif finish_reason!="completed":
        warnings.append("调用未完整结束；请检查草稿和任务状态。")
    if not structured and text:
        warnings.append("交付 JSON 不完整或不符合合同。可手动补齐正文，或主动重新生成。")
    if "USD" in text or "美元" in text:
        warnings.append("工作台预算单位是人民币；草稿中的美元描述需对照 Brief 修正。")
    if "未发生费用" in text or "没有费用" in text:
        warnings.append("模型不能判断本次调用费用；实际费用应以供应商账单为准。")
    return {"available":bool(text),"finish_reason":finish_reason,"text":text,"structured":structured,
            "draft_fields":{k:fields.get(k,"") for k in ("title","summary","body")},"warnings":warnings,
            "usage":usage or {},"complete":bool(structured and finish_reason=="completed")}


def save_output(root,task_id,view):
    folder=Path(root)/"task-outputs"
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f"{task_id}.json"
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(view,ensure_ascii=False),encoding="utf-8")
    temp.replace(path)


def read_output(root,task):
    path=Path(root)/"task-outputs"/f"{task['id']}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    session_id=task.get("session_id")
    if session_id:
        folder=Path(root)/"harness"/task["id"]/"sessions"
        for log in folder.glob(f"*/{session_id}/session.v3.jsonl"):
            text="";reason=None;usage={}
            with log.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row=json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("type")=="assistant/message":
                        data=row.get("data",{})
                        message=data.get("message",data)
                        text=''.join(b.get("text","") for b in message.get("content",[]) if b.get("type")=="text")
                        # Token counts only; SDK internals may add private data.
                        usage={k:v for k,v in (data.get("usage") or {}).items() if isinstance(v,(int,float))}
                    elif row.get("type")=="turn/end":
                        reason=row.get("data",{}).get("reason",{}).get("kind")
            view=output_view(text,reason,usage)
            if task.get("status") not in ("running","input_changed"):
                save_output(root,task["id"],view)
            return view
    return output_view("",None)

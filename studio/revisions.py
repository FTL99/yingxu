"""Revisiting roles and sharing confirmed changes, without regenerating media."""
import json

from .handoff import edge
from .store import DomainError


def editing(task):
    return bool(task.get("revision_open") or task.get("sync_pending"))


def discussion_refs(store, db, task, refs):
    """Use current inputs, excluding this role's own prior output and its descendants.

    Otherwise old downstream gates/assets can make a revision depend on itself.
    Excluded results remain in storage as history, never re-approved by this helper.
    """
    pid=task["project_id"]
    root=task.get("result_id")
    memo={}
    def downstream(record,seen=None):
        key=(record["kind"],record["id"],record["version"])
        if record["kind"]=="artifact" and record["id"]==root:
            return True
        if key in memo: return memo[key]
        seen=set(seen or ())
        if key in seen: return True
        seen.add(key)
        answer=any(downstream(store.record(db,pid,q["kind"],q["id"],q["version"]),seen) for q in record["input_refs"])
        memo[key]=answer
        return answer
    chosen,excluded=[],[]
    for q in refs:
        r=store.record(db,pid,q["kind"],q["id"])
        if r["stale"] or (root and downstream(r)):
            if q not in excluded: excluded.append(q)
            continue
        current=edge(r)
        if current not in chosen: chosen.append(current)
    return chosen,excluded


def project_changes(store,db,pid):
    changes=[];seen=set()
    for row in db.execute("SELECT data FROM events WHERE project_id=? AND type='role.settings_synced' ORDER BY seq DESC",(pid,)):
        item=json.loads(row["data"])
        q=item["artifact_ref"]
        if q["id"] in seen: continue
        seen.add(q["id"])
        r=store.record(db,pid,q["kind"],q["id"])
        if r["stale"] or r["version"]!=q["version"]: continue
        changes.append({**item,"body":r["data"]["body"],"uncertainties":r["data"].get("uncertainties",[])})
        if len(changes)==12: break
    return changes


def ensure_idle(db,pid):
    # Avoid changing a contract while a paid request or image queue is executing.
    for table in ("tasks","chat_jobs","media_jobs","exports"):
        if db.execute(f"SELECT 1 FROM {table} WHERE project_id=? AND status IN ('running','input_changed') LIMIT 1",(pid,)).fetchone():
            raise DomainError("项目仍有回复、生成或导出正在执行，请等它结束后再修改和同步设定。")
    if db.execute("SELECT 1 FROM image_batches WHERE project_id=? AND status IN ('queued','running') LIMIT 1",(pid,)).fetchone():
        raise DomainError("图片队列仍在执行，请先暂停队列或等它完成，再修改和同步设定。")

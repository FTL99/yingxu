"""Explicit, versioned information packages for downstream roles."""
import json

from .store import DomainError


def edge(record):
    return {"kind": record["kind"], "id": record["id"], "version": record["version"]}


def upstream_package(store, db, task, allow_pending=False):
    pid = task["project_id"]
    visited, ancestors, pending = set(), [], []

    def visit(tid):
        if tid in visited:
            return
        visited.add(tid)
        parent = store.task(db, tid, pid)
        for dep in parent["dependencies"]:
            visit(dep)
        if parent["status"] != "accepted" or not parent["result_id"] or parent.get("superseded_by"):
            if allow_pending:
                pending.append({"task_id":tid,"role":parent["role"],"status":parent["status"],"reason":"上游修改尚未确认，旧成果不能作为现行设定"})
                return
            raise DomainError("交接所需的上游成果尚未验收或已被替代")
        artifact = store.record(db, pid, "artifact", parent["result_id"])
        try:
            store.validate_refs(db, pid, [edge(artifact), *artifact["input_refs"]])
        except DomainError:
            if not allow_pending: raise
            pending.append({"task_id":tid,"role":parent["role"],"status":parent["status"],"reason":"上游成果引用的资料已变化，需重新确认"})
            return
        if artifact["data"].get("brief_revision") != task["brief_revision"]:
            raise DomainError("上游交接包使用了旧Brief，请先复查")
        ancestors.append({"task_id": tid, "role": parent["role"], "artifact_ref": edge(artifact),
                          "title": artifact["data"]["title"], "summary": artifact["data"]["summary"],
                          "body": artifact["data"]["body"], "uncertainties": artifact["data"].get("uncertainties", []),
                          "discussion": artifact["data"].get("discussion_snapshot", []),
                          "proposed_records": artifact["data"].get("proposed_records", [])})

    for dep in task["dependencies"]:
        visit(dep)
    return {"brief_revision": task["brief_revision"], "upstream": ancestors, **({"pending_upstream":pending} if allow_pending else {}),
            "rules": "已验收正文是当前决定；讨论用于理解取舍，早期建议不等于最终决定。未验证事项不能变成事实。"}


def discussion_snapshot(db, task):
    return [{"role": row["role"], "text": row["text"], "complete": bool(row["complete"])}
            for row in db.execute("SELECT role,text,complete FROM chat_messages WHERE project_id=? AND task_id=? ORDER BY rowid",
                                  (task["project_id"], task["id"]))]


def append_upstream_refs(refs, package):
    for parent in package["upstream"]:
        if parent["artifact_ref"] not in refs:
            refs.append(parent["artifact_ref"])


def proposed_hypotheses(result, owner):
    candidates = []
    for item in result.get("proposed_records", []):
        if item.get("kind") != "hypothesis":
            continue
        data = item["data"]
        claim = data.get("claim") or data.get("statement")
        if not isinstance(claim, str) or not claim.strip():
            continue
        candidates.append({"claim": claim, "owner": owner, "source_type": "model_inference",
            "evidence_status": "untested", "evidence": "", "risk": "高",
            "failure_impact": data.get("failure_impact") or data.get("loss_if_wrong") or "待补充",
            "test_plan": data.get("test_plan") or data.get("min_test") or data.get("verification_plan") or "待补充",
            "counter_criteria": data.get("counter_criteria") or data.get("falsified_when") or data.get("failure_condition") or "待约定"})
    return candidates

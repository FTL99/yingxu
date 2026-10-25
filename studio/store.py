import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class DomainError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "studio.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER, data TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS records(project_id TEXT, kind TEXT, id TEXT, version INTEGER, data TEXT, input_refs TEXT, stale INTEGER DEFAULT 0, created_at TEXT, PRIMARY KEY(project_id,kind,id,version));
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, project_id TEXT, role TEXT, goal TEXT, dependencies TEXT, input_refs TEXT, brief_revision INTEGER, prompt_version TEXT, status TEXT, result_id TEXT, error TEXT, session_id TEXT, reservation REAL DEFAULT 0, actual_cost REAL, cost_evidence TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, type TEXT, data TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS requests(project_id TEXT, action TEXT, request_id TEXT, fingerprint TEXT, result TEXT, PRIMARY KEY(project_id,action,request_id));
            CREATE TABLE IF NOT EXISTS chat_jobs(id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT, session_id TEXT, status TEXT, reservation REAL, actual_cost REAL, cost_evidence TEXT, error TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS chat_messages(id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT, job_id TEXT, role TEXT, text TEXT, result TEXT, complete INTEGER, created_at TEXT);
            """)
            columns={r["name"] for r in db.execute("PRAGMA table_info(tasks)")}
            if "superseded_by" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN superseded_by TEXT")
            if "merged_into" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN merged_into TEXT")
            for name,definition in (("revision_open","INTEGER DEFAULT 0"),("sync_pending","INTEGER DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            if "revision_message_id" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN revision_message_id TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def event(self, db, project_id, kind, data):
        db.execute("INSERT INTO events(project_id,type,data,created_at) VALUES(?,?,?,?)", (project_id, kind, dump(data), now()))

    def project(self, db, project_id):
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise DomainError("项目不存在", 404)
        return {"id": row["id"], "revision": row["revision"], **json.loads(row["data"]), "created_at": row["created_at"]}

    def records(self, db, project_id, kind=None):
        rows = db.execute("""SELECT r.* FROM records r JOIN (SELECT kind,id,MAX(version) v FROM records WHERE project_id=? GROUP BY kind,id) latest
        ON r.kind=latest.kind AND r.id=latest.id AND r.version=latest.v WHERE r.project_id=? ORDER BY r.created_at""", (project_id, project_id)).fetchall()
        return [self.record_dict(r) for r in rows if kind is None or r["kind"] == kind]

    def record_dict(self, row):
        return {**dict(row), "data": json.loads(row["data"]), "input_refs": json.loads(row["input_refs"]), "stale": bool(row["stale"])}

    def record(self, db, project_id, kind, record_id, version=None):
        if version is None:
            row = db.execute("SELECT * FROM records WHERE project_id=? AND kind=? AND id=? ORDER BY version DESC LIMIT 1", (project_id, kind, record_id)).fetchone()
        else:
            row = db.execute("SELECT * FROM records WHERE project_id=? AND kind=? AND id=? AND version=?", (project_id, kind, record_id, version)).fetchone()
        if not row:
            raise DomainError("引用的记录不存在", 404)
        return self.record_dict(row)

    def validate_refs(self, db, project_id, refs):
        for ref in refs:
            current = self.record(db, project_id, ref["kind"], ref["id"])
            if current["version"] != ref["version"] or current["stale"]:
                raise DomainError("输入版本已变化或需要复查，请重新选取当前有效版本")

    def save_record(self, db, project_id, kind, data, record_id=None, expected_version=0, refs=None, skip_task=None):
        self.project(db, project_id)
        refs = refs or []
        self.validate_refs(db, project_id, refs)
        record_id = record_id or uid(kind)
        previous = db.execute("SELECT MAX(version) FROM records WHERE project_id=? AND kind=? AND id=?", (project_id, kind, record_id)).fetchone()[0] or 0
        if previous != expected_version:
            raise DomainError("记录已被修改，请刷新后合并")
        if any(r["id"] == record_id and r["kind"] == kind for r in refs):
            raise DomainError("记录不能依赖自身", 422)
        def reaches_self(q, visited):
            edge=(q["kind"],q["id"])
            if edge==(kind,record_id):
                return True
            if edge in visited:
                return False
            visited.add(edge)
            parent=self.record(db,project_id,q["kind"],q["id"])
            return any(reaches_self(p,visited) for p in parent["input_refs"])
        if any(reaches_self(q,set()) for q in refs):
            raise DomainError("输入依赖形成循环，请拆分记录",422)
        version = previous + 1
        db.execute("INSERT INTO records(project_id,kind,id,version,data,input_refs,created_at) VALUES(?,?,?,?,?,?,?)", (project_id, kind, record_id, version, dump(data), dump(refs), now()))
        if previous:
            self.invalidate(db, project_id, {("record", kind, record_id)}, skip_task)
        self.event(db, project_id, "record.saved", {"kind": kind, "id": record_id, "version": version})
        return self.record(db, project_id, kind, record_id)

    def invalidate(self, db, project_id, changed, skip_task=None):
        # Follow only recorded dependency edges; unrelated work remains valid.
        seen = set(changed)
        modified = True
        while modified:
            modified = False
            for r in self.records(db, project_id):
                edge = ("record", r["kind"], r["id"])
                if edge not in seen and any(("record", q["kind"], q["id"]) in seen for q in r["input_refs"]):
                    db.execute("UPDATE records SET stale=1 WHERE project_id=? AND kind=? AND id=? AND version=?", (project_id, r["kind"], r["id"], r["version"]))
                    seen.add(edge)
                    modified = True
            for t in self.tasks(db, project_id):
                if t["id"] == skip_task:
                    continue
                edge = ("task", t["id"])
                affected = any(("record", q["kind"], q["id"]) in seen for q in t["input_refs"]) or any(("task", dep) in seen for dep in t["dependencies"])
                if edge not in seen and affected:
                    status = "input_changed" if t["status"] == "running" else "needs_review"
                    db.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (status, now(), t["id"]))
                    seen.add(edge)
                    if t["result_id"]:
                        seen.add(("record", "artifact", t["result_id"]))
                        db.execute("UPDATE records SET stale=1 WHERE project_id=? AND kind='artifact' AND id=?", (project_id, t["result_id"]))
                    modified = True
        if len(seen) > len(changed):
            self.event(db, project_id, "inputs.invalidated", {"affected": [list(e) for e in seen - changed]})
        return seen

    def task(self, db, task_id, project_id=None):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or (project_id and row["project_id"] != project_id):
            raise DomainError("任务不存在", 404)
        return {**dict(row), "dependencies": json.loads(row["dependencies"]), "input_refs": json.loads(row["input_refs"])}

    def tasks(self, db, project_id):
        return [self.task(db, r["id"]) for r in db.execute("SELECT id FROM tasks WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()]

    def dependencies_ready(self, db, task):
        if self.project(db, task["project_id"])["revision"] != task["brief_revision"]:
            raise DomainError("Brief已修改，请建立使用新Brief的任务")
        self.validate_refs(db, task["project_id"], task["input_refs"])
        for dep in task["dependencies"]:
            upstream = self.task(db, dep, task["project_id"])
            if upstream["status"] != "accepted" or not upstream["result_id"]:
                raise DomainError("上游任务尚未验收通过")
            output = self.record(db, task["project_id"], "artifact", upstream["result_id"])
            if output["stale"]:
                raise DomainError("上游成果需要复查")

    def recover(self):
        with self.transaction() as db:
            db.execute("UPDATE chat_jobs SET status='interrupted',error='进程中断；保留原会话与费用，不自动重试。' WHERE status='running'")
            rows = db.execute("SELECT id,project_id FROM tasks WHERE status IN ('running','input_changed')").fetchall()
            for r in rows:
                db.execute("UPDATE tasks SET status='interrupted',error=?,updated_at=? WHERE id=?", ("进程中断，外部结果可能已产生。核对会话后再提交成果或新建任务；不会自动重放。", now(), r["id"]))
                self.event(db, r["project_id"], "task.interrupted", {"task_id": r["id"]})

    def idempotent(self, db, project_id, action, request_id, payload, operation):
        if not request_id or len(request_id) > 200:
            raise DomainError("缺少有效的请求ID", 422)
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        previous = db.execute("SELECT fingerprint,result FROM requests WHERE project_id=? AND action=? AND request_id=?", (project_id, action, request_id)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise DomainError("同一请求ID不能用于不同内容")
            return json.loads(previous["result"])
        result = operation()
        db.execute("INSERT INTO requests VALUES(?,?,?,?,?)", (project_id, action, request_id, fingerprint, dump(result)))
        return result

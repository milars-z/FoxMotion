import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def now():
    return datetime.now(UTC).isoformat(timespec="microseconds")


class Store:
    def __init__(self, data: Path):
        self.root = data.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for part in ("inputs", "outputs", "temp"):
            (self.root / part).mkdir(exist_ok=True)
        self.path = self.root / "studio.db"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
            with self.connection(True) as migration:
                if "privacy_mode" not in {col[1] for col in migration.execute("PRAGMA table_info(jobs)")}:
                    migration.execute("ALTER TABLE jobs ADD COLUMN privacy_mode INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE TABLE IF NOT EXISTS trash (job_id TEXT PRIMARY KEY)")

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list(self, limit, offset):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def insert(self, db, job_id, req, inputs, backend):
        db.execute(
            """INSERT INTO jobs(job_id,mode,prompt,parameters_json,input_files_json,
                   backend,status,created_at,stage,privacy_mode) VALUES(?,?,?,?,?,?,'queued',?,'等待执行',?)""",
            (
                job_id,
                req.mode,
                "" if req.privacy_mode else req.prompt,
                req.parameters.model_dump_json(),
                "[]" if req.privacy_mode else json.dumps(inputs),
                backend,
                now(),
                int(req.privacy_mode),
            ),
        )
        return dict(
            db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        )

    def claim(self):
        with self.connection(True) as db:
            row = db.execute(
                "SELECT job_id FROM jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE jobs SET status='running',started_at=?,stage='准备执行' WHERE job_id=?",
                    (now(), row[0]),
                )
                return dict(
                    db.execute(
                        "SELECT * FROM jobs WHERE job_id=?", (row[0],)
                    ).fetchone()
                )
        return None

    def finish(self, job_id, status, stage, error=None, output=None):
        with self.connection(True) as db:
            row = db.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id=? AND status='running'",
                (job_id,),
            ).fetchone()
            if row and row[0] and status != "interrupted":
                status, stage, error, output = "canceled", "已取消", None, None
            db.execute(
                """UPDATE jobs SET status=?,stage=?,finished_at=?,error_message=?,output_path=?
                       WHERE job_id=? AND status='running'""",
                (status, stage, now(), error, output, job_id),
            )

    def field(self, job_id, field, value):
        if field not in ("stage", "comfy_prompt_id"):
            raise ValueError("未知字段")
        with self.connection(True) as db:
            db.execute(
                f"UPDATE jobs SET {field}=? WHERE job_id=? AND status='running'",
                (value, job_id),
            )

    def recover(self):
        with self.connection(True) as db:
            db.execute(
                """UPDATE jobs SET status='interrupted',stage='执行中断',finished_at=?,
                        error_message=CASE WHEN privacy_mode=1 THEN '保密执行中断，正在清理'\n                        ELSE 'Worker 上次执行中断，可重新生成。' END WHERE status='running'""",
                (now(),),
            )

    def heartbeat(self, backend):
        with self.connection(True) as db:
            db.execute(
                "INSERT OR REPLACE INTO worker_state VALUES(1,?,?)", (now(), backend)
            )

    def worker(self):
        with self.connection() as db:
            row = db.execute("SELECT * FROM worker_state WHERE id=1").fetchone()
        online = bool(
            row
            and (
                datetime.now(UTC) - datetime.fromisoformat(row["heartbeat"])
            ).total_seconds()
            < 12
        )
        return {"online": online, "backend": row["backend"] if row else None}

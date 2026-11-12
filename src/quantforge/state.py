"""持久化状态层：SQLite 检查点、事件日志、审批记录。

长程运行要求：
- 每个阶段完成即写检查点（幂等键 = run_id + stage_id + round）；
- 中断恢复时读取检查点，已完成的阶段直接跳过，不重复执行；
- 所有状态变更写事件日志（append-only），供审计与恢复证据。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS stage_runs (
    idempotency_key TEXT PRIMARY KEY,   -- run_id:stage_id:round
    run_id TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    round INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,               -- done / failed / skipped
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at REAL, finished_at REAL,
    result_ref TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, run_id TEXT, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, run_id TEXT, gate TEXT,
    decision TEXT, approver TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT
);
"""


class StateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)

    # ---- 检查点 ----
    def is_done(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM stage_runs WHERE idempotency_key=?", (key,)
        ).fetchone()
        return row is not None and row[0] == "done"

    def begin_stage(self, key: str, run_id: str, stage_id: str, rnd: int = 0) -> int:
        """登记阶段开始，返回当前尝试次数。已 done 的阶段不应被重复 begin。"""
        cur = self.conn.execute(
            "SELECT attempts FROM stage_runs WHERE idempotency_key=?", (key,)
        ).fetchone()
        attempts = (cur[0] if cur else 0) + 1
        self.conn.execute(
            """INSERT INTO stage_runs (idempotency_key, run_id, stage_id, round, status, attempts, started_at)
               VALUES (?,?,?,?, 'running', ?, ?)
               ON CONFLICT(idempotency_key) DO UPDATE SET
                   status='running', attempts=?, started_at=?""",
            (key, run_id, stage_id, rnd, attempts, time.time(), attempts, time.time()),
        )
        self.conn.commit()
        return attempts

    def complete_stage(self, key: str, result_ref: str = "") -> None:
        self.conn.execute(
            "UPDATE stage_runs SET status='done', finished_at=?, result_ref=? WHERE idempotency_key=?",
            (time.time(), result_ref, key),
        )
        self.conn.commit()

    def fail_stage(self, key: str, error: str) -> None:
        self.conn.execute(
            "UPDATE stage_runs SET status='failed', finished_at=?, error=? WHERE idempotency_key=?",
            (time.time(), error, key),
        )
        self.conn.commit()

    def stage_table(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT stage_id, round, status, attempts, error FROM stage_runs WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [dict(zip(("stage_id", "round", "status", "attempts", "error"), r)) for r in rows]

    # ---- 事件日志 ----
    def log(self, run_id: str, kind: str, detail: dict | str) -> None:
        self.conn.execute(
            "INSERT INTO events (ts, run_id, kind, detail) VALUES (?,?,?,?)",
            (time.time(), run_id, kind, json.dumps(detail, ensure_ascii=False, default=str)),
        )
        self.conn.commit()

    # ---- 键值状态（如"上次优化时的市场状态"、当前持仓）----
    def set_kv(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, json.dumps(value, ensure_ascii=False, default=str),
             json.dumps(value, ensure_ascii=False, default=str)),
        )
        self.conn.commit()

    def get_kv(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # ---- 人工审批记录 ----
    def record_approval(self, run_id: str, gate: str, decision: str,
                        approver: str, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO approvals (ts, run_id, gate, decision, approver, note) VALUES (?,?,?,?,?,?)",
            (time.time(), run_id, gate, decision, approver, note),
        )
        self.conn.commit()

    def recent_events(self, run_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ts, kind, detail FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",
            (run_id, limit),
        ).fetchall()
        return [{"ts": ts, "kind": k, "detail": d} for ts, k, d in rows]

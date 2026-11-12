"""策略版本注册表：冻结基线、候选、采纳、回滚、决策留痕。

规则：
- 版本 0 为冻结基线（frozen baseline），任何对比都以它为参照，永不被覆盖；
- 候选版本经核验 ACCEPT + 人工批准后才转为 approved（生效）；
- 回滚 = 把 approved 指针退回到任一历史 approved 版本，操作留痕。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .compute.backtest import BASELINE_PARAMS

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_versions (
    version_id TEXT PRIMARY KEY,
    parent_id TEXT,
    params TEXT NOT NULL,
    status TEXT NOT NULL,          -- baseline / candidate / approved / rejected / retired
    oos_metrics TEXT,
    decision TEXT,                 -- accept / reject / rollback / init
    decision_reason TEXT,
    created_at REAL NOT NULL
);
"""


class StrategyRegistry:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)
        if self.get("v0-baseline") is None:
            self._insert("v0-baseline", None, BASELINE_PARAMS, "baseline",
                         None, "init", "冻结基线：一切迭代的固定对照")

    def _insert(self, vid, parent, params, status, oos, decision, reason):
        self.conn.execute(
            """INSERT INTO strategy_versions
               (version_id, parent_id, params, status, oos_metrics, decision, decision_reason, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (vid, parent, json.dumps(params), status,
             json.dumps(oos) if oos else None, decision, reason, time.time()),
        )
        self.conn.commit()

    def get(self, vid: str) -> dict | None:
        r = self.conn.execute(
            "SELECT version_id, parent_id, params, status, oos_metrics, decision, decision_reason"
            " FROM strategy_versions WHERE version_id=?", (vid,)
        ).fetchone()
        if not r:
            return None
        return {"version_id": r[0], "parent_id": r[1], "params": json.loads(r[2]),
                "status": r[3], "oos_metrics": json.loads(r[4]) if r[4] else None,
                "decision": r[5], "decision_reason": r[6]}

    def approved_version(self) -> dict:
        r = self.conn.execute(
            "SELECT version_id FROM strategy_versions WHERE status='approved'"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        vid = r[0] if r else "v0-baseline"
        return self.get(vid)

    def new_version_id(self, params: dict) -> str:
        h = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
        return f"v-{h}"

    def register_candidate(self, parent_id: str, params: dict) -> dict:
        vid = self.new_version_id(params)
        if self.get(vid):
            return self.get(vid)
        self._insert(vid, parent_id, params, "candidate", None, None, None)
        return self.get(vid)

    def decide(self, vid: str, decision: str, reason: str, oos: dict | None = None) -> dict:
        """decision: accept / reject。accept 时新版本生效，旧 approved 退役（可回滚）。"""
        assert decision in ("accept", "reject")
        v = self.get(vid)
        assert v is not None, f"unknown version {vid}"
        if decision == "accept":
            self.conn.execute(
                "UPDATE strategy_versions SET status='retired' WHERE status='approved'")
            self.conn.execute(
                "UPDATE strategy_versions SET status='approved', decision='accept',"
                " decision_reason=?, oos_metrics=? WHERE version_id=?",
                (reason, json.dumps(oos) if oos else None, vid))
        else:
            self.conn.execute(
                "UPDATE strategy_versions SET status='rejected', decision='reject',"
                " decision_reason=?, oos_metrics=? WHERE version_id=?",
                (reason, json.dumps(oos) if oos else None, vid))
        self.conn.commit()
        return self.get(vid)

    def rollback(self, to_version: str, reason: str) -> dict:
        target = self.get(to_version)
        assert target and target["status"] in ("approved", "retired", "baseline"), \
            "只能回滚到历史 approved/retired/baseline 版本"
        self.conn.execute("UPDATE strategy_versions SET status='retired' WHERE status='approved'")
        self.conn.execute(
            "UPDATE strategy_versions SET status='approved', decision='rollback',"
            " decision_reason=? WHERE version_id=?", (reason, to_version))
        self.conn.commit()
        return self.get(to_version)

    def history(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT version_id, parent_id, status, decision, decision_reason FROM strategy_versions"
            " ORDER BY created_at").fetchall()
        return [dict(zip(("version_id", "parent_id", "status", "decision", "reason"), r)) for r in rows]

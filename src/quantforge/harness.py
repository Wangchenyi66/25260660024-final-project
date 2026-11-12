"""Harness / 控制器：确定性执行控制，与智能体决策分离。

职责：
- 按 config/workflow.yaml 的 DAG 调度阶段，阶段间依赖与顺序由本类保证；
- 检查点与幂等恢复：run_id+stage+round 为幂等键，已完成阶段跳过不重复执行；
- 重试（指数退避）、停止规则（连续核验失败→升级上报）、人工批准闸门；
- LLM 预算与工具权限的执行层强制（超限/越权即中止）。
智能体不感知调度、重试与恢复——它们只做判断与产物生成。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from .agents import (BacktestAgent, DataAgent, MonitorAgent, PortfolioAgent,
                     ResearchAgent, VerificationAgent)
from .compute.backtest import BASELINE_PARAMS
from .compute.datafeed import load_market, market_regime_label
from .llm import Budget, get_llm
from .registry import StrategyRegistry
from .state import StateStore


class AwaitingApproval(Exception):
    """重大操作前暂停，等待人工批准。检查点保留，批准后可恢复。"""


class HaltWorkflow(Exception):
    """触发停止规则（如连续核验失败），升级上报后终止。"""


class Harness:
    def __init__(self, root: str | Path = ".", auto_approve: bool = False,
                 crash_after: str | None = None, data_seed: int = 42):
        self.root = Path(root)
        self.artifacts_dir = self.root / "artifacts"
        self.artifacts_dir.mkdir(exist_ok=True)
        self.workflow = yaml.safe_load((self.root / "config/workflow.yaml").read_text())["workflow"]
        self.limits = yaml.safe_load((self.root / "config/limits.yaml").read_text())["limits"]
        self.monitor_cfg = {"degradation_margin": 0.30, "dd_limit": 0.15}

        self.state = StateStore(self.root / "state/quantforge.db")
        self.registry = StrategyRegistry(self.root / "state/registry.db")
        self.data = load_market("synthetic", seed=data_seed)
        self.llm = get_llm(Budget(**self.limits["run_budget"]))

        self.auto_approve = auto_approve
        self.crash_after = crash_after
        self.run_id = "unassigned"

    @property
    def today(self) -> str:
        return str(self.data["prices"].index[-1].date())

    # ---- 产物读取（供智能体与恢复路径使用）----
    def read_artifact(self, name: str, default=None):
        path = self.artifacts_dir / name
        if not path.exists():
            return default
        if path.suffix in (".json",):
            return json.loads(path.read_text())
        if path.suffix in (".yaml", ".yml"):
            return yaml.safe_load(path.read_text())
        return path.read_text()

    # ---- Mock 研究路径：规则化参数补丁（真实 LLM 由 prompts/ 引导）----
    def mock_patch(self, regime: str, rnd: int) -> dict:
        if regime == "high_vol":
            return ({"momentum_days": 15, "vol_weight": 0.6,
                     "momentum_weight": 0.2, "volume_weight": 0.2, "top_n": 3}
                    if rnd == 0 else
                    {"momentum_days": 10, "vol_weight": 0.6,
                     "momentum_weight": 0.2, "volume_weight": 0.2, "top_n": 3})
        return ({"momentum_days": 30, "top_n": 2} if rnd == 0 else
                {"momentum_days": 40, "vol_weight": 0.4})

    # ---- 阶段执行：检查点 + 重试 + 崩溃注入 ----
    def _run_stage(self, run_id: str, stage_id: str, fn, rnd: int = 0):
        key = f"{run_id}:{stage_id}:{rnd}"
        if self.state.is_done(key):
            self.state.log(run_id, "skip_checkpoint", {"stage": stage_id, "round": rnd})
            return None
        attempts = self.state.begin_stage(key, run_id, stage_id, rnd)
        max_att = self.limits["retries"]["max_attempts"]
        backoff = self.limits["retries"]["backoff_seconds"]
        while True:
            try:
                result = fn()
                self.state.complete_stage(key, result_ref=stage_id)
                self.state.log(run_id, "stage_done",
                               {"stage": stage_id, "round": rnd, "attempts": attempts})
                if self.crash_after == stage_id:
                    raise RuntimeError(f"[injected crash] 阶段 {stage_id} 完成后进程被杀")
                return result
            except Exception as e:  # noqa: BLE001
                if str(e).startswith("[injected crash]"):
                    raise
                self.state.fail_stage(key, str(e))
                if attempts >= max_att:
                    self.state.log(run_id, "stage_failed_final",
                                   {"stage": stage_id, "error": str(e)})
                    raise
                attempts = self.state.begin_stage(key, run_id, stage_id, rnd)
                time.sleep(backoff * attempts)  # 指数退避
        return None

    # ---- 人工批准闸门（检查点式：批准记录持久化，恢复后不再重复询问）----
    def _gate_approved(self, run_id: str, gate: str) -> bool:
        rows = self.state.conn.execute(
            "SELECT decision FROM approvals WHERE run_id=? AND gate=?", (run_id, gate)
        ).fetchall()
        return any(r[0] == "approve" for r in rows)

    def _human_gate(self, run_id: str, gate: str) -> None:
        if self._gate_approved(run_id, gate):
            return
        if self.auto_approve:
            self.state.record_approval(run_id, gate, "approve", "demo-auto",
                                       "演示模式自动批准；生产环境必须人工签字")
            self.state.log(run_id, "gate_approved", {"gate": gate, "mode": "auto"})
            return
        req = self.artifacts_dir / f"approval_request_{run_id}_{gate}.md"
        req.write_text(f"# 人工批准请求\n\n- run: {run_id}\n- gate: {gate}\n"
                       f"- 请审查 artifacts/ 下相关报告后批准或拒绝。\n")
        self.state.log(run_id, "gate_waiting", {"gate": gate, "file": req.name})
        raise AwaitingApproval(f"等待人工批准: {gate}（见 {req.name}）")

    def approve(self, run_id: str, gate: str, approver: str, note: str = "") -> None:
        self.state.record_approval(run_id, gate, "approve", approver, note)

    # ---- 停止规则：连续核验失败升级上报 ----
    def _bump_rejects(self) -> int:
        n = int(self.state.get_kv("consecutive_rejects", 0)) + 1
        self.state.set_kv("consecutive_rejects", n)
        if n >= self.limits["stop_rules"]["consecutive_verification_failures"]:
            esc = self.root / self.limits["stop_rules"]["escalation_file"]
            esc.write_text(
                f"# 升级上报\n\n连续 {n} 次策略核验 REJECT，自动迭代已停止。\n"
                f"请人工介入：检查数据质量、核验报告与研究员假设方向。\n")
            self.state.log(self.run_id, "escalation", {"file": esc.name, "rejects": n})
            raise HaltWorkflow(f"连续核验失败 {n} 次，已升级上报（{esc.name}）")
        return n

    # ---- 迭代主循环（含检查点恢复与有界修订循环）----
    def run_iteration(self, run_id: str, trigger: str = "scheduled") -> dict:
        self.run_id = run_id
        self.state.log(run_id, "run_start", {"trigger": trigger})
        self._run_stage(run_id, "data_check", lambda: DataAgent().run(self))

        max_rounds = self.workflow["revision_loop"]["max_rounds"]
        rnd = 0
        verdict = None
        while True:
            feedback = verdict["feedback_for_research"] if verdict else None
            self._run_stage(run_id, "hypothesis",
                            lambda: ResearchAgent().run(self, rnd, feedback), rnd)
            self._run_stage(run_id, "backtest",
                            lambda: BacktestAgent().run(self, rnd), rnd)
            v = self._run_stage(run_id, "verification",
                                lambda: VerificationAgent().run(self, rnd), rnd)
            verdict = v or self.read_artifact(f"verification_report_r{rnd}.json")
            if verdict["verdict"] == "ACCEPT":
                self.state.set_kv("consecutive_rejects", 0)
                break
            rnd += 1
            if rnd > max_rounds:
                card = self.read_artifact(f"hypothesis_r{max_rounds}.yaml")
                cand = self.registry.register_candidate(card["parent_version"], card["merged_params"])
                self.registry.decide(cand["version_id"], "reject",
                                     "核验未通过且修订轮次耗尽", verdict)
                self.state.log(run_id, "iteration_rejected", {"rounds": max_rounds})
                self._bump_rejects()  # 停止规则按"整轮迭代失败"计数，而非按修订轮计数
                return {"run_id": run_id, "outcome": "rejected", "rounds": max_rounds}

        # 人工闸门 → 晋级 → 指令
        self._human_gate(run_id, "promote_strategy")
        card = self.read_artifact(f"hypothesis_r{rnd}.yaml")
        cand = self.registry.register_candidate(card["parent_version"], card["merged_params"])
        self.registry.decide(cand["version_id"], "accept",
                             f"核验ACCEPT：日均差CI下界={verdict['ci95_lower_bound']['mean_daily_ret']:.2e}",
                             self.read_artifact(f"backtest_report_r{rnd}.json")["oos_metrics"])
        self.state.set_kv("regime_at_last_optimization", market_regime_label(self.data))
        self.state.log(run_id, "promoted", {"version": cand["version_id"]})

        self._human_gate(run_id, "generate_orders")
        orders = self._run_stage(run_id, "orders", lambda: PortfolioAgent().run(self))
        self.state.log(run_id, "run_done", {"version": cand["version_id"]})
        return {"run_id": run_id, "outcome": "promoted",
                "version": cand["version_id"], "orders": orders}

    # ---- 日常监测：衰减触发迭代 ----
    def monitor_and_maybe_iterate(self, run_id: str) -> dict:
        self.run_id = run_id
        rep = self._run_stage(run_id, "monitor", lambda: MonitorAgent().run(self))
        rep = rep or self.read_artifact("monitor_report.json")
        if rep["trigger_iteration"]:
            self.state.log(run_id, "trigger", {"reasons": rep["reasons"]})
            return self.run_iteration(f"{run_id}-iter", trigger="degradation")
        return {"run_id": run_id, "outcome": "no_trigger"}

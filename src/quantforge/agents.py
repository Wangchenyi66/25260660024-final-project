"""六个智能体：职责单一、工具受权限矩阵约束、产物落盘为 JSON/YAML。

分工原则（对应 DESIGN.md）：
- 智能体只做"判断与汇总"，一切数值计算由 compute/ 确定性代码完成；
- 核验员与研究员相互独立（不读对方解释性文字，只看数值证据）；
- 组合员只能读取 approved 版本；监测员只能触发迭代，不能改策略。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from .compute import backtest as bt
from .compute import lookahead as la
from .compute import metrics as mt
from .compute.datafeed import market_regime_label


def oos_backtest(data: dict, params: dict) -> dict:
    """walk-forward 样本外聚合回测：各折测试窗收益拼接后统一计量。"""
    wins = bt.walk_forward_windows(len(data["prices"]))
    folds = []
    oos_ret = []
    for w in wins:
        te0, te1 = w["test"]
        r = bt.run_backtest(data, params, start_idx=te0, end_idx=te1)
        folds.append({"test_window": [te0, te1], **r["metrics"]})
        oos_ret.append(r["daily_ret"])
    oos = pd.concat(oos_ret)
    return {"oos_metrics": mt.summarize(oos), "folds": folds,
            "windows": wins, "oos_daily_ret": oos}


class Agent:
    name = "agent"
    tools: set[str] = set()

    def check_tool(self, tool: str) -> None:
        if tool not in self.tools:
            raise PermissionError(f"{self.name} 无权调用工具 {tool}（见 config/limits.yaml）")

    def _write(self, h, filename: str, payload) -> Path:
        self.check_tool("write_artifact")
        path = h.artifacts_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, (dict, list)):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            path.write_text(str(payload))
        h.state.log(h.run_id, "artifact", {"agent": self.name, "file": filename})
        return path


class DataAgent(Agent):
    """数据工程师：新鲜度、完整性、点时正确性。FAIL 则拦截整个工作流。"""
    name = "data_agent"
    tools = {"read_file", "write_artifact", "run_integrity_checks"}

    def run(self, h) -> dict:
        self.check_tool("run_integrity_checks")
        prices, regime = h.data["prices"], h.data["regime"]
        idx = prices.index
        report = {
            "as_of": str(idx[-1].date()),
            "n_assets": prices.shape[1], "n_days": len(prices),
            "fresh": True,  # 合成数据即时生成；真实数据在此比较最后日期与截止时点
            "missing_values": int(prices.isna().sum().sum()),
            "timestamp_monotonic": bool(idx.is_monotonic_increasing and idx.is_unique),
            "records_after_cutoff": 0,  # 生成器按定义不含未来记录；真实数据在此检查
            "regime_label": market_regime_label(h.data),
            "verdict": "PASS",
        }
        if report["missing_values"] or not report["timestamp_monotonic"]:
            report["verdict"] = "FAIL"
        self._write(h, "data_quality_report.json", report)
        if report["verdict"] == "FAIL":
            raise RuntimeError("数据质量 FAIL：工作流停止于 data_check（harness 执行控制）")
        return report


class ResearchAgent(Agent):
    """策略研究员：基于监测证据提出参数级迭代假设（假设卡）。"""
    name = "research_agent"
    tools = {"read_file", "write_artifact", "propose_hypothesis"}

    def run(self, h, rnd: int = 0, feedback: str | None = None) -> dict:
        self.check_tool("propose_hypothesis")
        monitor = h.read_artifact("monitor_report.json", default={})
        data_rep = h.read_artifact("data_quality_report.json", default={})
        if data_rep.get("verdict") == "FAIL":
            raise RuntimeError("数据不合格，研究员禁止提出假设")

        parent = h.registry.approved_version()
        regime = monitor.get("regime_now", data_rep.get("regime_label", "low_vol"))
        # Mock 路径：规则化补丁（真实 LLM 路径由 prompts/research_agent.md 引导生成）
        patch = h.mock_patch(regime, rnd)
        card = {
            "hypothesis_id": f"H-{h.today}-{rnd:02d}",
            "rationale": (
                f"监测显示市场状态为 {regime}，滚动60日Calmar="
                f"{monitor.get('rolling_60d', {}).get('calmar')}，基线="
                f"{monitor.get('baseline_60d', {}).get('calmar')}；"
                f"{'针对核验反馈修订：' + feedback if feedback else '按状态切换规则调整参数'}"
            ),
            "parent_version": parent["version_id"],
            "params_patch": patch,
            "market_regime_note": f"当前状态 {regime}",
            "expected_effect": "样本外 Calmar/Sharpe 改进且回撤不恶化",
            "revision_round": rnd,
        }
        p = bt.validate_params({**parent["params"], **patch})
        card["merged_params"] = p
        self._write(h, f"hypothesis_r{rnd}.yaml", yaml.safe_dump(card, allow_unicode=True))
        return card


class BacktestAgent(Agent):
    """回测执行：纯确定性计算，无 LLM 参与。"""
    name = "backtest_agent"
    tools = {"read_file", "write_artifact", "run_backtest"}

    def run(self, h, rnd: int = 0) -> dict:
        self.check_tool("run_backtest")
        card = yaml.safe_load((h.artifacts_dir / f"hypothesis_r{rnd}.yaml").read_text())
        res = oos_backtest(h.data, card["merged_params"])
        report = {
            "hypothesis_id": card["hypothesis_id"],
            "params": card["merged_params"],
            "oos_metrics": res["oos_metrics"],
            "folds": res["folds"],
        }
        self._write(h, f"backtest_report_r{rnd}.json", report)
        return report


class VerificationAgent(Agent):
    """独立核验员：未来函数 lint + 固定种子复算 + 冻结基线对比。一票否决。"""
    name = "verification_agent"
    tools = {"read_file", "write_artifact", "run_backtest",
             "run_lookahead_lint", "compare_baseline"}

    def run(self, h, rnd: int = 0) -> dict:
        self.check_tool("run_lookahead_lint"); self.check_tool("compare_baseline")
        data_rep = h.read_artifact("data_quality_report.json", default={})
        card = yaml.safe_load((h.artifacts_dir / f"hypothesis_r{rnd}.yaml").read_text())
        bt_rep = h.read_artifact(f"backtest_report_r{rnd}.json")

        violations = la.full_lookahead_lint(h.data, card["merged_params"]) \
            if data_rep.get("verdict") == "PASS" else ["[gate] 数据质量 FAIL"]

        # 可复现性：确定性重跑，指标必须一致
        rerun = oos_backtest(h.data, card["merged_params"])["oos_metrics"]
        reproducible = all(
            abs(rerun[k] - bt_rep["oos_metrics"][k]) < 1e-8
            for k in ("calmar", "sharpe", "ann_return"))

        # 固定基线对比（同一 walk-forward 窗口）
        base = oos_backtest(h.data, bt.BASELINE_PARAMS)
        cand_ret = oos_backtest(h.data, card["merged_params"])["oos_daily_ret"]
        # 统计策略：日均收益差的 bootstrap CI 下界 > -2bp（不显著为负）；
        # 经济显著性：Calmar 改进 > 0.05 且 Sharpe 改进 > 0；回撤不恶化。
        ci_low = mt.bootstrap_ci(cand_ret, base["oos_daily_ret"], lambda s: float(s.mean()))
        calmar_d = rerun["calmar"] - base["oos_metrics"]["calmar"]
        sharpe_d = rerun["sharpe"] - base["oos_metrics"]["sharpe"]
        econ_ok = calmar_d > 0.05 and sharpe_d > 0
        stat_ok = ci_low > -2e-4
        # max_dd 为负数；恶化 = 候选回撤比基线再深 20% 以上
        dd_worse = rerun["max_dd"] < base["oos_metrics"]["max_dd"] * 1.2
        accept = (not violations) and reproducible and econ_ok and stat_ok and not dd_worse

        report = {
            "verdict": "ACCEPT" if accept else "REJECT",
            "lookahead_violations": violations,
            "reproducible": reproducible,
            "baseline_delta": {"calmar": calmar_d, "sharpe": sharpe_d},
            "economic_thresholds": {"calmar_min": 0.05, "sharpe_min": 0.0},
            "ci95_lower_bound": {"mean_daily_ret": ci_low, "stat_bar": -2e-4},
            "max_dd_worsened": bool(dd_worse),
            "reject_reasons": ([] if accept else
                               (violations + ([] if reproducible else ["结果不可复现"])
                                + ([] if econ_ok else ["未达经济显著阈值(Calmar>0.05且Sharpe>0)"])
                                + ([] if stat_ok else ["日均收益差CI下界≤-2bp，改进不成立"])
                                + ([] if not dd_worse else ["回撤恶化超容忍"]))),
            "feedback_for_research": "" if accept else
                "缩小参数改动幅度或改变迭代方向，重新针对当前市场状态设计",
        }
        self._write(h, f"verification_report_r{rnd}.json", report)
        return report


class PortfolioAgent(Agent):
    """组合构建：只读 approved 版本，产出 dry-run 调仓指令。"""
    name = "portfolio_agent"
    tools = {"read_file", "write_artifact", "read_approved_version", "write_orders_dryrun"}

    def run(self, h) -> dict:
        self.check_tool("read_approved_version"); self.check_tool("write_orders_dryrun")
        v = h.registry.approved_version()
        if v["status"] not in ("approved", "baseline"):
            raise RuntimeError("无已批准版本，禁止生成指令")
        p = bt.validate_params(v["params"])
        score = bt.compute_signals(h.data["prices"], h.data["volumes"], p).iloc[-1].dropna()
        top = score.nlargest(p["top_n"]).index
        target = {s: 1.0 / p["top_n"] for s in top}
        current = h.state.get_kv("current_holdings", {})
        rows = []
        for s in sorted(set(target) | set(current)):
            tw = target.get(s, 0.0)
            side = "BUY" if tw > current.get(s, 0.0) else ("SELL" if s in current else "HOLD")
            if abs(tw - current.get(s, 0.0)) > 1e-9:
                rows.append({"date": h.today, "symbol": s, "side": side,
                             "target_weight": round(tw, 4), "est_cost_bps": 10})
        out = h.artifacts_dir / "orders_dryrun.csv"
        pd.DataFrame(rows, columns=["date", "symbol", "side", "target_weight", "est_cost_bps"]
                     ).to_csv(out, index=False)
        h.state.set_kv("current_holdings", target)
        self._write(h, "orders_meta.json", {"version": v["version_id"], "n_orders": len(rows)})
        return {"version": v["version_id"], "orders": rows}


class MonitorAgent(Agent):
    """绩效监测：与回测共用同一代码路径；只触发，不修改。"""
    name = "monitor_agent"
    tools = {"read_file", "write_artifact", "run_backtest", "trigger_iteration"}

    def run(self, h) -> dict:
        self.check_tool("run_backtest")
        v = h.registry.approved_version()
        p = bt.validate_params(v["params"])
        res = bt.run_backtest(h.data, p)
        base = bt.run_backtest(h.data, bt.BASELINE_PARAMS)
        tail = lambda s: s.iloc[-60:]
        roll, base_roll = mt.summarize(tail(res["daily_ret"])), mt.summarize(tail(base["daily_ret"]))
        dd_now = mt.max_drawdown(res["daily_ret"].iloc[-120:])
        regime_now = market_regime_label(h.data)
        regime_then = h.state.get_kv("regime_at_last_optimization", "low_vol")

        reasons = []
        if roll["calmar"] < base_roll["calmar"] - h.monitor_cfg["degradation_margin"]:
            reasons.append(f"滚动Calmar {roll['calmar']:.2f} 低于基线 "
                           f"{base_roll['calmar']:.2f} 超过阈值 {h.monitor_cfg['degradation_margin']}")
        if abs(dd_now) > h.monitor_cfg["dd_limit"]:
            reasons.append(f"当前回撤 {dd_now:.2%} 超过限制 {h.monitor_cfg['dd_limit']}")
        if regime_now != regime_then:
            reasons.append(f"市场状态切换 {regime_then} → {regime_now}")
        report = {
            "as_of": h.today, "strategy_version": v["version_id"],
            "rolling_60d": roll, "baseline_60d": base_roll,
            "current_dd_120d": dd_now,
            "regime_now": regime_now, "regime_at_last_optimization": regime_then,
            "trigger_iteration": bool(reasons), "reasons": reasons,
        }
        self._write(h, "monitor_report.json", report)
        return report

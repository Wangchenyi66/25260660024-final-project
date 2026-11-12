"""Harness 执行控制证据：重试退避、人工闸门、停止规则升级上报。"""
import pytest

from quantforge.agents import DataAgent
from quantforge.harness import AwaitingApproval, Harness, HaltWorkflow


def _prep(tmp_path):
    import shutil
    from pathlib import Path
    proj = Path(__file__).resolve().parents[1]
    shutil.copytree(proj / "config", tmp_path / "config")
    return tmp_path


def test_retry_with_exponential_backoff(tmp_path, monkeypatch):
    h = Harness(root=_prep(tmp_path), auto_approve=True)
    calls = {"n": 0}
    original = DataAgent.run

    def flaky(self, hh):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("瞬时故障（模拟）")
        return original(self, hh)

    monkeypatch.setattr(DataAgent, "run", flaky)
    h._run_stage("run-retry", "data_check", lambda: DataAgent().run(h))
    row = {s["stage_id"]: s for s in h.state.stage_table("run-retry")}["data_check"]
    assert row["status"] == "done" and row["attempts"] == 3  # 3 次尝试后成功


def test_stage_fails_permanently_after_max_attempts(tmp_path, monkeypatch):
    h = Harness(root=_prep(tmp_path), auto_approve=True)

    def always_fail(self, hh):
        raise RuntimeError("永久故障（模拟）")

    monkeypatch.setattr(DataAgent, "run", always_fail)
    with pytest.raises(RuntimeError, match="永久故障"):
        h._run_stage("run-fail", "data_check", lambda: DataAgent().run(h))
    row = {s["stage_id"]: s for s in h.state.stage_table("run-fail")}["data_check"]
    assert row["attempts"] == h.limits["retries"]["max_attempts"]


def test_human_gate_blocks_then_allows(tmp_path):
    h = Harness(root=_prep(tmp_path), auto_approve=False)
    with pytest.raises(AwaitingApproval):
        h._human_gate("run-g", "promote_strategy")
    h.approve("run-g", "promote_strategy", "测试审批人", "已审阅")
    h._human_gate("run-g", "promote_strategy")  # 不再抛异常（批准记录已持久化）


def test_stop_rule_escalates_after_consecutive_rejects(tmp_path):
    h = Harness(root=_prep(tmp_path), auto_approve=True)
    h._bump_rejects()
    h._bump_rejects()
    with pytest.raises(HaltWorkflow):
        h._bump_rejects()
    esc = tmp_path / h.limits["stop_rules"]["escalation_file"]
    assert esc.exists(), "连续失败必须产生升级上报文件"
    assert "连续 3 次" in esc.read_text()


def test_tool_permission_matrix_enforced():
    from quantforge.agents import PortfolioAgent
    agent = PortfolioAgent()
    with pytest.raises(PermissionError):
        agent.check_tool("run_backtest")  # 组合员无权回测
    agent.check_tool("write_orders_dryrun")  # 有权指令

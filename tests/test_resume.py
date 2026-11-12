"""中断恢复证据：崩溃后重启，已完成阶段不重复执行，工作流推进到完成。"""
import pytest

from quantforge.harness import Harness


def test_crash_and_resume_without_repeating_completed_stages(tmp_path, monkeypatch):
    root = tmp_path
    # 把项目配置复制到临时 root，使 Harness 在隔离环境运行
    import shutil
    proj = __import__("pathlib").Path(__file__).resolve().parents[1]
    shutil.copytree(proj / "config", root / "config")

    h1 = Harness(root=root, auto_approve=True, crash_after="backtest")
    with pytest.raises(RuntimeError, match="injected crash"):
        h1.run_iteration("run-t1")
    done = {s["stage_id"]: s for s in h1.state.stage_table("run-t1")}
    assert done["backtest"]["status"] == "done"
    assert done["backtest"]["attempts"] == 1

    h2 = Harness(root=root, auto_approve=True)
    res = h2.run_iteration("run-t1")
    assert res["outcome"] == "promoted"

    stages = {s["stage_id"]: s for s in h2.state.stage_table("run-t1")}
    # 崩溃前完成的三个阶段：尝试次数仍为 1（恢复后未重复执行）
    for sid in ("data_check", "hypothesis", "backtest"):
        assert stages[sid]["attempts"] == 1, f"{sid} 被重复执行了"
    # 恢复后完成的阶段
    assert stages["verification"]["status"] == "done"
    assert stages["orders"]["status"] == "done"
    # 事件日志中存在跳过记录
    kinds = [e["kind"] for e in h2.state.recent_events("run-t1", 50)]
    assert "skip_checkpoint" in kinds

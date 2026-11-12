"""基线对比与版本治理证据：好补丁被采纳、坏补丁被拒绝、版本可回滚。"""
from quantforge.compute import backtest as bt
from quantforge.compute.datafeed import load_market
from quantforge.harness import Harness
from quantforge.registry import StrategyRegistry


def test_verification_accepts_good_patch(tmp_path):
    h = Harness(root=_prep(tmp_path), auto_approve=True)
    res = h.run_iteration("run-acc")
    assert res["outcome"] == "promoted"
    v = h.registry.approved_version()
    assert v["version_id"] != "v0-baseline"
    assert v["status"] == "approved"
    # 样本外指标确实优于基线
    rep = h.read_artifact("backtest_report_r0.json")
    assert rep["oos_metrics"]["calmar"] > 0


def test_verification_rejects_bad_patch_and_rolls_back(tmp_path, monkeypatch):
    """强制研究员提出坏补丁（正动量参数在反转市必然受损）→ 核验拒绝 → 版本治理正确。"""
    h = Harness(root=_prep(tmp_path), auto_approve=True)
    monkeypatch.setattr(h, "mock_patch",
                        lambda regime, rnd: {"momentum_days": 20, "top_n": 5,
                                             "momentum_weight": 0.7, "vol_weight": 0.1,
                                             "volume_weight": 0.2})
    res = h.run_iteration("run-rej")
    assert res["outcome"] == "rejected"
    assert h.registry.approved_version()["version_id"] == "v0-baseline"  # 未被污染
    hist = h.registry.history()
    assert any(r["decision"] == "reject" for r in hist)


def test_rollback_restores_previous_version(tmp_path):
    h = Harness(root=_prep(tmp_path), auto_approve=True)
    h.run_iteration("run-rb")
    promoted = h.registry.approved_version()["version_id"]
    assert promoted != "v0-baseline"
    h.registry.rollback("v0-baseline", "演示回滚：新版本上线后表现不及预期")
    assert h.registry.approved_version()["version_id"] == "v0-baseline"


def test_frozen_baseline_is_immutable(tmp_path):
    reg = StrategyRegistry(tmp_path / "reg.db")
    v0 = reg.get("v0-baseline")
    assert v0["status"] == "baseline"
    assert v0["params"] == bt.BASELINE_PARAMS


def _prep(tmp_path):
    import shutil
    from pathlib import Path
    proj = Path(__file__).resolve().parents[1]
    shutil.copytree(proj / "config", tmp_path / "config")
    return tmp_path

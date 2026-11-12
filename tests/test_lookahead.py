"""未来函数检测的证据：干净引擎必须零违规；故意泄露必须被抓到。"""
from quantforge.compute import backtest as bt
from quantforge.compute import lookahead as la
from quantforge.compute.datafeed import load_market


def test_clean_engine_passes_all_lints():
    data = load_market("synthetic", seed=42)
    violations = la.full_lookahead_lint(data, bt.BASELINE_PARAMS)
    assert violations == [], f"干净引擎不应有违规: {violations}"


def test_leaky_signal_is_caught(monkeypatch):
    """人为制造未来泄露（信号不平移、直接用未来收益打分），检测器必须抓到。"""
    data = load_market("synthetic", seed=42)

    def leaky_signals(prices, volumes, params):
        future_ret = prices.shift(-1) / prices - 1  # 明日收益 = 未来数据
        return future_ret

    monkeypatch.setattr(bt, "compute_signals", leaky_signals)
    violations = la.full_lookahead_lint(data, bt.BASELINE_PARAMS)
    assert any("截断" in v or "lag" in v for v in violations), \
        f"泄露信号必须被截断复算抓到: {violations}"

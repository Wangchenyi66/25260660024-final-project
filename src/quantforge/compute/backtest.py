"""确定性回测引擎：动量-低波行业轮动策略。

防未来函数纪律（写死，供 lookahead-lint 技能核验）：
- 信号在 t 日收盘计算，t+1 日收益才计入持仓（持仓滞后一日生效）；
- 所有 rolling 窗口右端 = 决策日，禁止中心化窗口与未来引用；
- walk-forward 划分中训练窗与测试窗之间设 embargo 隔离带。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics as mt

PARAM_BOUNDS = {
    "momentum_days": (5, 60),
    "vol_days": (5, 60),
    "top_n": (2, 8),
    "momentum_weight": (0.1, 0.8),
    "vol_weight": (0.1, 0.8),
    "volume_weight": (0.1, 0.8),
    "rebalance_freq": ("W", "M"),
}

BASELINE_PARAMS = {
    "momentum_days": 20, "vol_days": 20, "top_n": 3,
    "momentum_weight": 0.5, "vol_weight": 0.3, "volume_weight": 0.2,
    "rebalance_freq": "W",
}

EMBARGO_DAYS = 5  # walk-forward 隔离带：训练窗与测试窗之间的隔离交易日数


def validate_params(params: dict) -> dict:
    p = dict(BASELINE_PARAMS)
    p.update({k: v for k, v in params.items() if k in PARAM_BOUNDS})
    for k, (lo, hi) in PARAM_BOUNDS.items():
        if isinstance(lo, str):
            assert p[k] in (lo, hi), f"{k} must be one of {(lo, hi)}"
        else:
            p[k] = type(lo)(max(lo, min(hi, p[k])))
    return p


def compute_signals(prices: pd.DataFrame, volumes: pd.DataFrame, params: dict) -> pd.DataFrame:
    """t 日信号：仅使用 ≤ t 的数据。返回打分矩阵（行=交易日）。"""
    mom = prices / prices.shift(params["momentum_days"]) - 1
    vol = prices.pct_change().rolling(params["vol_days"]).std()
    vz = (np.log(volumes) - np.log(volumes).rolling(20).mean()) / \
         np.log(volumes).rolling(20).std()
    score = (params["momentum_weight"] * mom
             - params["vol_weight"] * vol
             + params["volume_weight"] * vz.fillna(0))
    return score


def compute_positions(score: pd.DataFrame, params: dict) -> pd.DataFrame:
    """调仓日（周末/月末信号日）等权持有打分 top_n；持仓权重矩阵。"""
    dates = score.index
    rebal = pd.Series(False, index=dates)
    if params["rebalance_freq"] == "W":
        rebal[dates.weekday == 4] = True
    else:
        rebal[dates.is_month_end] = True
    weights = pd.DataFrame(0.0, index=dates, columns=score.columns)
    current = pd.Series(0.0, index=score.columns)
    for t in dates:
        if rebal.loc[t]:
            s = score.loc[t].dropna()
            if len(s) >= params["top_n"]:
                top = s.nlargest(params["top_n"]).index
                current = pd.Series(0.0, index=score.columns)
                current[top] = 1.0 / params["top_n"]
        weights.loc[t] = current
    return weights


def run_backtest(data: dict, params: dict, cost_bps: float = 10.0,
                 start_idx: int = 0, end_idx: int | None = None) -> dict:
    """执行回测。信号在 t 日生成，持仓收益从 t+1 日起算（shift(1) 纪律）。"""
    p = validate_params(params)
    prices, volumes = data["prices"], data["volumes"]
    score = compute_signals(prices, volumes, p)
    weights = compute_positions(score, p)

    ret = prices.pct_change()
    held = weights.shift(1).fillna(0.0)            # ← 防未来函数：持仓滞后一日生效
    gross = (held * ret).sum(axis=1)
    turnover = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    net = gross - turnover * cost_bps / 1e4
    net = net.iloc[start_idx:end_idx]
    return {
        "params": p,
        "daily_ret": net,
        "equity": (1 + net).cumprod(),
        "turnover": turnover.iloc[start_idx:end_idx],
        "metrics": mt.summarize(net),
    }


def walk_forward_windows(n_days: int, n_folds: int = 3, train: int = 250,
                         test: int = 120) -> list[dict]:
    """walk-forward 窗口划分，训练窗与测试窗之间留 EMBARGO_DAYS 隔离带。"""
    wins = []
    for i in range(n_folds):
        tr0 = i * test
        tr1 = tr0 + train
        te0 = tr1 + EMBARGO_DAYS
        te1 = te0 + test
        if te1 > n_days:
            break
        wins.append({"train": (tr0, tr1), "test": (te0, te1)})
    return wins

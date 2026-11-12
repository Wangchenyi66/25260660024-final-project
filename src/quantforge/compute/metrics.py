"""绩效指标（纯函数，确定性计算）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def annual_return(daily_ret: pd.Series) -> float:
    eq = (1 + daily_ret).prod()
    return float(eq ** (TRADING_DAYS / max(len(daily_ret), 1)) - 1)


def annual_vol(daily_ret: pd.Series) -> float:
    return float(daily_ret.std(ddof=0) * np.sqrt(TRADING_DAYS))


def sharpe(daily_ret: pd.Series, rf: float = 0.0) -> float:
    v = annual_vol(daily_ret)
    if v == 0:
        return 0.0
    return float((annual_return(daily_ret) - rf) / v)


def max_drawdown(daily_ret: pd.Series) -> float:
    eq = (1 + daily_ret).cumprod()
    dd = eq / eq.cummax() - 1
    return float(dd.min())


def calmar(daily_ret: pd.Series) -> float:
    mdd = abs(max_drawdown(daily_ret))
    if mdd == 0:
        return 0.0
    return float(annual_return(daily_ret) / mdd)


def summarize(daily_ret: pd.Series) -> dict:
    return {
        "ann_return": annual_return(daily_ret),
        "ann_vol": annual_vol(daily_ret),
        "sharpe": sharpe(daily_ret),
        "max_dd": max_drawdown(daily_ret),
        "calmar": calmar(daily_ret),
        "n_days": int(len(daily_ret)),
    }


def bootstrap_ci(daily_ret_a: pd.Series, daily_ret_b: pd.Series,
                 metric, n_boot: int = 1000, seed: int = 7) -> float:
    """对 metric(A)-metric(B) 做 bootstrap，返回 95% 置信区间下界（单侧）。

    用于核验阶段的"改进是否显著"判断（简化版 deflated 指标思想）。
    """
    rng = np.random.default_rng(seed)
    a, b = daily_ret_a.values, daily_ret_b.values
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        diffs[i] = metric(pd.Series(a[idx])) - metric(pd.Series(b[idx]))
    return float(np.percentile(diffs, 5))

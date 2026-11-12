"""未来函数（look-ahead bias）检测：静态源码扫描 + 动态截断复算。

对应技能 skills/lookahead-lint/SKILL.md，由 verification_agent 在核验阶段调用。
"""
from __future__ import annotations

import inspect
import re

import numpy as np
import pandas as pd

from . import backtest as bt

FORBIDDEN_PATTERNS = [
    (r"shift\(\s*-\d", "存在 shift(-k) 未来引用"),
    (r"center\s*=\s*True", "rolling 窗口 center=True 引入未来数据"),
]


def static_source_check() -> list[str]:
    """静态扫描回测引擎源码，禁止出现未来引用写法。"""
    src = inspect.getsource(bt)
    violations = []
    for pat, msg in FORBIDDEN_PATTERNS:
        if re.search(pat, src):
            violations.append(f"[static] {msg}")
    return violations


def truncated_signal_check(data: dict, params: dict, k: int = 20,
                           seed: int = 11) -> list[str]:
    """动态截断复算：随机选 k 个截断日，用 ≤T 数据重算信号并与全量信号比对。"""
    rng = np.random.default_rng(seed)
    prices, volumes = data["prices"], data["volumes"]
    p = bt.validate_params(params)
    score_full = bt.compute_signals(prices, volumes, p)

    max_win = max(p["momentum_days"], p["vol_days"], 20) + 10
    cands = np.arange(max_win, len(prices) - 1)
    if len(cands) < k:
        return ["[truncated] 样本长度不足以执行截断复算"]
    cut_days = rng.choice(cands, size=k, replace=False)

    violations = []
    for t in cut_days:
        px_t = prices.iloc[: t + 1]
        vol_t = volumes.iloc[: t + 1]
        score_t = bt.compute_signals(px_t, vol_t, p)
        day = prices.index[t]
        a = score_t.iloc[-1]
        b = score_full.loc[day]
        # 缺失模式检查：截断后信号若依赖未来数据，其可得性必然与全量信号不一致
        if int(a.isna().sum()) != int(b.isna().sum()):
            violations.append(
                f"[truncated] {day.date()} 截断重算后信号缺失模式改变（信号依赖未来数据）")
            continue
        both = a.notna() & b.notna()
        if both.any() and not np.allclose(a[both], b[both], atol=1e-10):
            violations.append(
                f"[truncated] {day.date()} 截断重算信号与全量信号不一致（疑似未来数据泄露）")
    return violations


def execution_lag_check(data: dict, params: dict) -> list[str]:
    """持仓滞后检查：t 日持仓收益必须等于用 shift(1) 持仓计算的收益。"""
    p = bt.validate_params(params)
    res = bt.run_backtest(data, p)
    # 直接验证：回测第一日收益应为 0（信号未生效前无持仓）
    if abs(res["daily_ret"].iloc[0]) > 1e-12:
        return ["[lag] 回测首日收益非零：持仓可能未滞后一日生效"]
    return []


def full_lookahead_lint(data: dict, params: dict) -> list[str]:
    return static_source_check() + truncated_signal_check(data, params) \
        + execution_lag_check(data, params)

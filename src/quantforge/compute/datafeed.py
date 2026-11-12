"""确定性数据层：合成行情生成（含市场状态切换）+ 可选真实数据源适配。

设计要点：
- 默认使用带 regime shift（低波动→高波动）的几何布朗运动合成数据，
  使"市场状态变化 → 触发策略迭代"的完整链路可在离线环境复现；
- 所有随机数均来自显式种子，保证回测可复现（核验阶段的固定种子复算依赖于此）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ASSET_NAMES = [f"ASSET_{i:02d}" for i in range(8)]


def generate_synthetic_market(
    seed: int = 42,
    n_days: int = 750,
    n_assets: int = 8,
    regime_shift_day: int = 500,
    start: str = "2023-01-03",
) -> dict:
    """生成含状态切换的合成行情。

    前半段低波动（年化 12%）、后半段高波动（年化 35%），且后半段动量效应减弱，
    用于演示 monitor 检测衰减并触发迭代循环。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n_days)

    low_vol, high_vol = 0.12 / np.sqrt(252), 0.35 / np.sqrt(252)
    vols = np.where(np.arange(n_days) < regime_shift_day, low_vol, high_vol)
    drift = np.where(np.arange(n_days) < regime_shift_day, 0.0004, 0.0001)

    # 共同市场因子 + 个体噪声；高波动状态下因子间相关性上升
    corr_low, corr_high = 0.3, 0.6
    market = rng.normal(drift, vols)
    closes = np.empty((n_days, n_assets))
    for j in range(n_assets):
        rho_series = np.where(np.arange(n_days) < regime_shift_day, corr_low, corr_high)
        beta_j = rng.uniform(0.7, 1.3)
        idio = rng.normal(0, vols * np.sqrt(1 - rho_series**2))
        # 前半段动量较强（趋势延续），后半段反转为反转市（短期动量反向）
        mom_boost = np.where(np.arange(n_days) < regime_shift_day, 0.15, -0.15)
        ret_j = beta_j * market + idio + mom_boost * np.roll(market, 5)
        ret_j[:5] = 0.0  # 避免 roll 引入的头部污染
        closes[:, j] = 100 * np.exp(np.cumsum(ret_j))

    prices = pd.DataFrame(closes, index=dates, columns=ASSET_NAMES[:n_assets])
    volumes = pd.DataFrame(
        rng.lognormal(mean=14, sigma=0.3, size=(n_days, n_assets)),
        index=dates, columns=prices.columns,
    )
    regime = pd.Series(
        np.where(np.arange(n_days) < regime_shift_day, "low_vol", "high_vol"),
        index=dates, name="regime",
    )
    return {"prices": prices, "volumes": volumes, "regime": regime,
            "regime_shift_day": regime_shift_day, "seed": seed}


def load_market(source: str = "synthetic", **kwargs) -> dict:
    """统一数据入口。source='synthetic' 离线可复现；'akshare' 为可选真实数据适配（需网络，不在默认路径）。"""
    if source == "synthetic":
        return generate_synthetic_market(**kwargs)
    if source == "akshare":
        raise NotImplementedError(
            "真实数据源适配器未默认启用：期末项目要求仓库不含凭据、且演示可离线复现。"
            "接入方法见 README「接入真实行情」一节。")
    raise ValueError(f"unknown source: {source}")


def market_regime_label(data: dict, as_of_idx: int | None = None) -> str:
    """返回给定位置的市场状态标签（供 monitor 与 data_check 使用）。"""
    r = data["regime"]
    return str(r.iloc[as_of_idx if as_of_idx is not None else -1])

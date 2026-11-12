# 核验与防未来信息证据

## 本次迭代的核验报告（verification_report_r0.json）
```json
{
  "verdict": "ACCEPT",
  "lookahead_violations": [],
  "reproducible": true,
  "baseline_delta": {
    "calmar": 0.07743475500853453,
    "sharpe": 0.09311613347078829
  },
  "economic_thresholds": {
    "calmar_min": 0.05,
    "sharpe_min": 0.0
  },
  "ci95_lower_bound": {
    "mean_daily_ret": -5.7584474031259195e-05,
    "stat_bar": -0.0002
  },
  "max_dd_worsened": false,
  "reject_reasons": [],
  "feedback_for_research": ""
}
```

## 核验的三道未来函数检测
1. **静态源码扫描**：回测引擎禁止未来引用写法（shift 负值、中心化窗口）；
2. **动态截断复算**：20 个随机截断日，用 ≤T 数据重算信号，与全量信号比对
   （数值 + 缺失模式双重一致性）；
3. **持仓滞后检查**：信号 t 日生成、t+1 日生效，回测首日收益必须为零。

反向证据（故意注入泄露必须被抓到）见 `tests/test_lookahead.py::test_leaky_signal_is_caught`。

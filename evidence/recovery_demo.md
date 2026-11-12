# 中断恢复证据

**方法**：在 backtest 阶段完成后注入进程崩溃（`crash_after="backtest"`），
随后以同一 run_id 重启 `run_iteration`，检查已完成的阶段不被重复执行。

## 崩溃前阶段表
```
[('data_check', 'done'), ('hypothesis', 'done'), ('backtest', 'done')]
```

## 恢复后阶段表（attempts 仍为 1 → 无重复执行）
```
[('data_check', 'done', 1), ('hypothesis', 'done', 1), ('backtest', 'done', 1), ('verification', 'done', 1), ('orders', 'done', 1)]
```

## 恢复时跳过检查点的阶段
```
['backtest', 'hypothesis', 'data_check']
```

结论：进程崩溃后，data_check / hypothesis / backtest 三个阶段经幂等键
（run_id:stage:round）检查直接跳过，未重复执行；工作流从 verification 继续推进到完成。
自动化测试见 `tests/test_resume.py`。

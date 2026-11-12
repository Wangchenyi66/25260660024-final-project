"""AlphaForge 一键演示：完整跑通"监测→触发→迭代→核验→晋级→指令"与恢复证据。

用法：
    python3 run_demo.py            # 完整演示（离线、确定性、约 1 分钟）
演示结束会刷新 examples/ 与 evidence/ 下的示例与证据文件。
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from quantforge.harness import AwaitingApproval, Harness  # noqa: E402


def fresh():
    for d in ("state", "artifacts"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    (ROOT / "artifacts").mkdir(exist_ok=True)


def banner(t):
    print(f"\n{'='*70}\n{t}\n{'='*70}")


def scenario_a():
    banner("场景 A：日常监测 → 衰减触发 → 自动迭代 → 核验晋级 → 调仓指令")
    h = Harness(root=ROOT, auto_approve=True)
    res = h.monitor_and_maybe_iterate("demo-daily")
    mon = h.read_artifact("monitor_report.json")
    print(f"监测结论: trigger={mon['trigger_iteration']}")
    for r in mon["reasons"]:
        print(f"  - {r}")
    print(f"迭代结果: {res['outcome']}  version={res.get('version')}")
    ver = h.read_artifact("verification_report_r0.json")
    print(f"核验判定: {ver['verdict']}  CalmarΔ={ver['baseline_delta']['calmar']:+.3f} "
          f"SharpeΔ={ver['baseline_delta']['sharpe']:+.3f}  未来函数违规={len(ver['lookahead_violations'])}")
    print("注册表:")
    for r in h.registry.history():
        print(f"  {r['version_id']:<14} {r['status']:<10} decision={r['decision']}")
    print("调仓指令(dry-run):")
    print((ROOT / "artifacts/orders_dryrun.csv").read_text())
    return h


def scenario_b():
    banner("场景 B：进程在回测完成后被杀 → 重启恢复（不重复已完成阶段）")
    h1 = Harness(root=ROOT, auto_approve=True, crash_after="backtest")
    try:
        h1.run_iteration("demo-resume", trigger="scheduled")
    except RuntimeError as e:
        print(f"注入崩溃: {e}")
    before = h1.state.stage_table("demo-resume")
    print("崩溃前阶段表:", [(s["stage_id"], s["status"]) for s in before])

    h2 = Harness(root=ROOT, auto_approve=True)
    res = h2.run_iteration("demo-resume", trigger="scheduled")
    after = h2.state.stage_table("demo-resume")
    print(f"恢复结果: {res['outcome']}")
    print("恢复后阶段表(attempts 均为 1 表示无重复执行):",
          [(s["stage_id"], s["status"], s["attempts"]) for s in after])
    skips = [json.loads(e["detail"])["stage"]
             for e in h2.state.recent_events("demo-resume", 50) if e["kind"] == "skip_checkpoint"]
    print("被跳过的已完成阶段:", skips)
    return h1, h2, before, after, skips


def scenario_c():
    banner("场景 C：人工批准闸门（非自动模式）")
    h = Harness(root=ROOT, auto_approve=False)
    try:
        h.run_iteration("demo-gate", trigger="scheduled")
    except AwaitingApproval as e:
        print(f"闸门挂起: {e}")
    print("人工审阅核验报告后批准两个闸门……")
    h.approve("demo-gate", "promote_strategy", "演示审批人", "已审阅核验报告，同意晋级")
    try:
        h.run_iteration("demo-gate", trigger="scheduled")
    except AwaitingApproval as e:
        print(f"第二闸门挂起: {e}")
    h.approve("demo-gate", "generate_orders", "演示审批人", "同意生成 dry-run 指令")
    res = h.run_iteration("demo-gate", trigger="scheduled")
    print(f"批准后恢复完成: {res['outcome']} version={res.get('version')}")
    rows = h.state.conn.execute(
        "SELECT gate, decision, approver FROM approvals WHERE run_id='demo-gate'").fetchall()
    print("审批留痕:", rows)
    return h, rows


def export_examples(h_a):
    banner("导出示例输入/输出与证据")
    ex_in, ex_out = ROOT / "examples/inputs", ROOT / "examples/outputs"
    for d in (ex_in, ex_out):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "artifacts/hypothesis_r0.yaml", ex_in / "hypothesis_card_example.yaml")
    for f in ("monitor_report.json", "data_quality_report.json",
              "backtest_report_r0.json", "verification_report_r0.json",
              "orders_dryrun.csv"):
        shutil.copy(ROOT / "artifacts" / f, ex_out / f)
    print("examples/inputs:", [p.name for p in ex_in.iterdir()])
    print("examples/outputs:", [p.name for p in ex_out.iterdir()])


def write_evidence(h_a, before, after, skips, approval_rows):
    ev = ROOT / "evidence"
    ev.mkdir(exist_ok=True)
    ver = h_a.read_artifact("verification_report_r0.json")

    (ev / "recovery_demo.md").write_text(f"""# 中断恢复证据

**方法**：在 backtest 阶段完成后注入进程崩溃（`crash_after="backtest"`），
随后以同一 run_id 重启 `run_iteration`，检查已完成的阶段不被重复执行。

## 崩溃前阶段表
```
{[(s['stage_id'], s['status']) for s in before]}
```

## 恢复后阶段表（attempts 仍为 1 → 无重复执行）
```
{[(s['stage_id'], s['status'], s['attempts']) for s in after]}
```

## 恢复时跳过检查点的阶段
```
{skips}
```

结论：进程崩溃后，data_check / hypothesis / backtest 三个阶段经幂等键
（run_id:stage:round）检查直接跳过，未重复执行；工作流从 verification 继续推进到完成。
自动化测试见 `tests/test_resume.py`。
""")

    (ev / "verification_demo.md").write_text(f"""# 核验与防未来信息证据

## 本次迭代的核验报告（verification_report_r0.json）
```json
{json.dumps(ver, ensure_ascii=False, indent=2)}
```

## 核验的三道未来函数检测
1. **静态源码扫描**：回测引擎禁止未来引用写法（shift 负值、中心化窗口）；
2. **动态截断复算**：20 个随机截断日，用 ≤T 数据重算信号，与全量信号比对
   （数值 + 缺失模式双重一致性）；
3. **持仓滞后检查**：信号 t 日生成、t+1 日生效，回测首日收益必须为零。

反向证据（故意注入泄露必须被抓到）见 `tests/test_lookahead.py::test_leaky_signal_is_caught`。
""")

    (ev / "human_gate_demo.md").write_text(f"""# 人工批准闸门证据

非自动模式下，两个重大操作（策略晋级、生成指令）必须人工批准；
挂起期间检查点保留，批准后恢复且不再重复询问。

## 审批留痕（state/quantforge.db approvals 表）
```
{approval_rows}
```

非演示环境请使用 `Harness(auto_approve=False)`，审批人会收到
`artifacts/approval_request_*.md` 并据此在系统中签字。
""")


def main():
    fresh()
    h_a = scenario_a()
    _, _, before, after, skips = scenario_b()
    _, rows = scenario_c()
    export_examples(h_a)
    write_evidence(h_a, before, after, skips, rows)
    banner("演示完成：examples/ 与 evidence/ 已刷新")


if __name__ == "__main__":
    main()

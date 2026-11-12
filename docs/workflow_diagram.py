"""生成带注释的工作流图 docs/workflow_diagram.png（可复现）。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

C_AGENT = "#DCE9F7"   # 智能体（LLM 判断）
C_DET = "#E4F2DE"     # 确定性计算
C_CTRL = "#FDE8D7"    # 控制层（Harness）
C_STORE = "#EFE3F5"   # 存储
C_GATE = "#F9D7D7"    # 人工闸门

fig, ax = plt.subplots(figsize=(16, 9))
ax.set_xlim(0, 16); ax.set_ylim(0, 9); ax.axis("off")

def box(x, y, w, h, text, color, fs=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.12",
                                fc=color, ec="#555", lw=1.2))
    ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fs)

def arrow(x1, y1, x2, y2, label="", color="#333", style="-|>", ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=14, color=color, lw=1.4, linestyle=ls))
    if label:
        ax.text((x1+x2)/2, (y1+y2)/2 + 0.18, label, ha="center", fontsize=9, color="#B03030")

# 泳道
ax.axhspan(5.6, 9, xmin=0.02, xmax=0.98, color="#F7F9FC", zorder=0)
ax.text(0.3, 8.6, "智能体层（LLM 判断）", fontsize=11, weight="bold", color="#35618F")
ax.text(0.3, 5.0, "控制层 / 确定性计算（Harness 执行）", fontsize=11, weight="bold", color="#4C7A3F")

# 主流程（上层）
box(0.4, 6.6, 2.0, 1.1, "触发器\n每日16:30 / 每周六\n衰减触发", C_CTRL)
box(3.0, 6.6, 2.0, 1.1, "DataAgent\n数据质量与点时检查\n(PASS/FAIL)", C_AGENT)
box(5.6, 6.6, 2.0, 1.1, "ResearchAgent\n提出迭代假设\n(假设卡)", C_AGENT)
box(8.2, 6.6, 2.0, 1.1, "BacktestAgent\n确定性回测\n(walk-forward+embargo)", C_DET)
box(10.8, 6.6, 2.0, 1.1, "VerificationAgent\n独立核验\n(一票否决)", C_AGENT)
box(13.4, 6.6, 2.2, 1.1, "MonitorAgent\n每日绩效监测\n对比冻结基线", C_AGENT)

arrow(2.4, 7.15, 3.0, 7.15)
arrow(5.0, 7.15, 5.6, 7.15, "PASS")
arrow(7.6, 7.15, 8.2, 7.15)
arrow(10.2, 7.15, 10.8, 7.15)

# 修订循环（REJECT 回路）
arrow(11.3, 6.6, 6.3, 5.75, "REJECT：反馈修订，最多3轮", color="#B03030")
arrow(6.3, 5.75, 6.3, 6.6, color="#B03030")
ax.text(7.2, 5.45, "有界修订循环（config/workflow.yaml: max_rounds=3）", fontsize=9, color="#B03030")

# 监测回路：monitor → 触发器
arrow(14.5, 7.7, 14.5, 8.35, "")
arrow(14.5, 8.35, 1.4, 8.35, "")
arrow(1.4, 8.35, 1.4, 7.7, "衰减/状态切换")
ax.text(7.5, 8.5, "每日监测闭环：异常即触发新一轮迭代", fontsize=9, color="#35618F")

# 下层：晋级与执行链
box(3.0, 3.2, 2.0, 1.0, "人工闸门①\n策略晋级批准", C_GATE)
box(5.6, 3.2, 2.0, 1.0, "Registry\n版本登记/回滚\n(冻结基线v0)", C_CTRL)
box(8.2, 3.2, 2.0, 1.0, "人工闸门②\n生成指令批准", C_GATE)
box(10.8, 3.2, 2.0, 1.0, "PortfolioAgent\ndry-run 调仓指令\n(只读approved)", C_AGENT)

arrow(12.3, 6.6, 4.0, 4.2, "ACCEPT", color="#2F7A2F")
arrow(5.0, 3.7, 5.6, 3.7)
arrow(7.6, 3.7, 8.2, 3.7)
arrow(10.2, 3.7, 10.8, 3.7)

# 存储层
box(1.2, 0.8, 4.6, 1.3, "SQLite 持久化状态 (state/quantforge.db)\n检查点(幂等键 run:stage:round) · 事件日志 · 审批记录 · kv状态", C_STORE, fs=9)
box(6.4, 0.8, 4.2, 1.3, "版本注册表 (state/registry.db)\nbaseline冻结 · candidate→approved/rejected\n决策留痕 · 可回滚", C_STORE, fs=9)
box(11.2, 0.8, 4.0, 1.3, "产物 artifacts/\n假设卡/回测报告/核验报告\n监测报告/指令CSV", C_STORE, fs=9)

for x in (4.0, 6.6, 9.2, 11.8):
    arrow(x, 3.2 if x in (4.0, 6.6, 9.2) else 3.2, x, 2.1, "", color="#888", ls="--")
ax.text(8.0, 2.35, "每阶段完成即写检查点（崩溃恢复不重复执行）", fontsize=9, color="#666")

# 停止规则注释
ax.text(0.4, 0.18, "重试：指数退避 ≤3 次 ｜ 连续 3 次迭代失败 → ESCALATION.md 升级人工 ｜ "
                  "预算：30 次 LLM 调用 / 3 万 token ｜ 权限：工具最小矩阵",
        fontsize=9, color="#7A4C2F",
        bbox=dict(boxstyle="round,pad=0.4", fc="#FFF7EE", ec="#D9B48F"))

ax.set_title("AlphaForge 工作流图：个人股票量化交易 AI 系统（长程智能体工作流）",
             fontsize=14, weight="bold", pad=14)
plt.tight_layout()
plt.savefig("docs/workflow_diagram.png", dpi=160, bbox_inches="tight")
print("saved docs/workflow_diagram.png")

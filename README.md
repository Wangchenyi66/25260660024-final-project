# AlphaForge — 个人股票量化交易 AI 系统（期末项目）

- **课程**：金融人工智能编程（2026 年秋季）
- **姓名**：王晨懿
- **学号**：25260660024
- **项目选题**：个人股票量化交易 AI 系统——可根据市场行情自动优化迭代升级的长程智能体工作流

## 项目概述

本项目为一个"个人股票量化交易策略的持续研究与迭代"任务设计并实现了端到端的长程智能体工作流 **AlphaForge**。系统每个交易日收盘后自动监测已生效策略的绩效与市场状态，当检测到衰减（滚动 Calmar 低于基线、回撤超限、波动率状态切换）时，自动触发一轮策略迭代：数据检查 → 研究员提出假设 → 确定性回测 → 独立核验（防未来函数 + 固定基线对比）→ 人工批准 → 版本登记 → dry-run 调仓指令，全程检查点持久化、可中断恢复、可回滚。

完整设计说明见 **[DESIGN.md](DESIGN.md)**，带注释的工作流图见 **[docs/workflow_diagram.png](docs/workflow_diagram.png)**。

## 代码仓库

- **GitHub**：https://github.com/Wangchenyi66/25260660024-final-project （Public）
- 本 ZIP 内含完整 Git 开发历史（隐藏目录 `.git/`，非浅拷贝，全部提交可离线审阅）：

```bash
git log --oneline   # 查看分阶段提交历史
```

## 文件说明

```
├── README.md                  # 本文件
├── DESIGN.md                  # 设计说明（任务定义、架构、长程运行、核验改进）
├── docs/
│   ├── workflow_diagram.py    # 工作流图生成脚本（可复现）
│   └── workflow_diagram.png   # 带注释的工作流图
├── config/
│   ├── workflow.yaml          # 阶段 DAG、触发器、修订循环、信息截止策略
│   └── limits.yaml            # 时间/成本/重试/停止规则/人工闸门/工具权限矩阵
├── prompts/                   # 六个智能体的系统提示词
├── skills/                    # 可复用技能包（假设卡/未来函数检测/QUBO组合）
├── src/quantforge/
│   ├── harness.py             # 控制器：调度/检查点/重试/闸门/预算/停止规则
│   ├── state.py               # SQLite 持久化（检查点/事件/审批/kv）
│   ├── registry.py            # 策略版本注册表（冻结基线/采纳/拒绝/回滚）
│   ├── llm.py                 # LLM 客户端（真实 API + 离线 Mock）与预算
│   ├── agents.py              # 数据/研究/回测/核验/组合/监测 六智能体
│   └── compute/               # 确定性计算：行情/回测/指标/未来函数检测
├── tests/                     # 12 个测试（泄露/恢复/基线/回滚/重试/闸门/停止规则）
├── examples/
│   ├── inputs/                # 示例输入（假设卡）
│   └── outputs/               # 示例输出（监测/回测/核验报告、指令 CSV）
├── evidence/                  # 恢复、核验、人工闸门 的演示证据
├── run_demo.py                # 一键演示脚本
└── requirements.txt
```

## 环境配置与运行说明

环境：Python ≥ 3.10（开发于 macOS，numpy/pandas/PyYAML/matplotlib/pytest）。

```bash
pip install -r requirements.txt   # 安装依赖

python3 run_demo.py               # 一键演示：三个场景约 1 分钟，全程离线
python3 -m pytest tests/ -v       # 运行全部测试（约 1 分钟）
python3 docs/workflow_diagram.py  # 重新生成工作流图
```

演示完全离线、确定可复现：未配置 LLM 密钥时自动使用 MockLLM。
接入真实 LLM：设置环境变量 `LLM_API_KEY`（可选 `LLM_BASE_URL`、`LLM_MODEL`），
密钥绝不写入仓库。

接入真实行情：实现 `src/quantforge/compute/datafeed.py` 中 `load_market` 的
`akshare` 分支（需自行安装 akshare 并配置网络），默认合成数据不含任何外部依赖。

## 个人贡献说明

本人独立完成本项目的选题、架构设计、工作流图绘制、全部代码实现、测试与文档撰写；
使用确定性合成数据与离线 MockLLM 保证全部演示可复现；设计并验证了中断恢复、
独立核验、人工闸门、版本回滚等长程工作流机制（详见 DESIGN.md 与 evidence/）。

## 安全说明

本仓库不含任何凭据、密钥或个人隐私数据；`.gitignore` 已排除运行时状态
（`state/`、`artifacts/`）；全部提交历史可离线审阅。

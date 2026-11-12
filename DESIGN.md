# AlphaForge 设计说明
## 个人股票量化交易 AI 系统：随行情自动优化迭代的长程智能体工作流

> 本文对应期末项目要求第 1、2 节。工作流图见 `docs/workflow_diagram.png`（由 `docs/workflow_diagram.py` 可复现生成）。

---

## 1. 任务定义

### 1.1 任务与目标用户

**任务**：个人股票量化交易策略的持续研究与迭代——从市场状态监测出发，自动完成"假设提出 → 数据准备 → 回测 → 稳健性核验 → 组合构建 → 持续监测"的闭环，并根据行情变化自动触发新一轮优化。

**目标用户**：个人量化投资者（本人）。管理一个 8 只资产的模拟行业轮动策略，追求样本外风险调整后收益的持续改进。

### 1.2 现有流程与痛点

人工流程：周末手工复盘 → 凭经验改参数 → 手动跑回测 → 凭感觉决定是否上线 → 平时不盯盘。痛点：
- 迭代无记录、不可复现、无法回滚；
- 样本内过拟合无法识别，未来函数无人检查；
- 行情突变（如波动率状态切换）后策略失效，发现滞后数周。

### 1.3 阶段、依赖、触发、输入与信息截止

| 阶段 | 依赖 | 触发条件 | 输入 | 输出产物 |
|---|---|---|---|---|
| data_check 数据检查 | — | 每次运行开始 | 行情数据、截止时点配置 | data_quality_report.json |
| hypothesis 假设 | data_check PASS | 迭代循环 | 监测报告、生效版本 | hypothesis_r{n}.yaml |
| backtest 回测 | hypothesis | 假设卡落盘 | 假设卡、行情（≤截止日） | backtest_report_r{n}.json |
| verification 核验 | backtest | 回测报告落盘 | 假设卡+回测报告+冻结基线 | verification_report_r{n}.json |
| 人工闸门① 晋级 | verification ACCEPT | 核验通过 | 核验报告 | approval_request / 审批记录 |
| promote 版本登记 | 闸门①批准 | — | 核验报告、参数 | promotion（注册表记录） |
| 人工闸门② 指令 | promote | 版本生效 | 生效版本 | 审批记录 |
| orders 组合构建 | 闸门②批准 | — | 生效版本、最新行情 | orders_dryrun.csv |
| monitor 日常监测 | — | **每日 16:30 收盘后**、**每周六 10:00** | 生效版本、最新行情 | monitor_report.json |
| 迭代循环 | monitor 触发 | **衰减触发**：滚动 Calmar 低于基线超阈值 / 回撤超限 / 市场状态切换 | 监测报告 | 回到 hypothesis |

**允许使用的信息来源**：本地合成行情生成器（离线可复现，默认）；可选 akshare 真实行情适配器（需自行配置，不随仓库分发）。禁止任何未声明的外部数据源。

**信息截止时点**：任何阶段只允许使用信号生成时点之前的数据（point-in-time）。交易日 T 收盘后运行，仅允许 T 及更早数据；walk-forward 划分带 5 个交易日隔离带（embargo）。

### 1.4 产出与验收标准

- **产出**：策略版本（注册表）、调仓指令（dry-run CSV）、核验报告、监测报告、全程事件日志。
- **验收标准**：
  1. 任何版本晋级必须同时通过：未来函数三道检测为零违规、固定种子复算一致、样本外 Calmar 改进 > 0.05 且 Sharpe 改进 > 0（经济显著）、日均收益差 bootstrap CI 下界 > -2bp（统计无害）、回撤不恶化超 20%；
  2. 中断恢复后不重复任何已完成阶段（测试证明）；
  3. 连续 3 次迭代失败自动停止并升级人工；
  4. 全部决策（采纳/拒绝/回滚/批准）留痕且可回滚。

### 1.5 为什么必须是持久化工作流，而不是一次性脚本

1. **跨天/跨周运行**：监测每日触发、迭代每周触发，状态（生效版本、当前持仓、上次优化时的市场状态、滚动绩效）必须跨进程持久化；
2. **中断恢复**：进程随时可能被杀（部署、断电、异常），恢复时不得重复已完成回测等昂贵操作；
3. **人机异步**：人工批准闸门可能挂起数小时，批准前后工作流状态必须一致；
4. **审计与回滚**：每次迭代的输入、证据、决策需要长期留痕，才能支持"对照基线、决定采纳、执行回滚"的治理闭环。

---

## 2. 系统架构

### 2.1 角色与交接

| 智能体 | 职责（决策） | 工具（受权限矩阵约束） | 技能 | 输入 → 输出 |
|---|---|---|---|---|
| DataAgent 数据工程师 | 判断数据是否可用（PASS/FAIL） | read_file, write_artifact, run_integrity_checks | — | 行情 → 数据质量报告 |
| ResearchAgent 策略研究员 | 基于监测证据提出参数级迭代假设 | read_file, write_artifact, propose_hypothesis | hypothesis-card | 监测报告+生效版本 → 假设卡 |
| BacktestAgent 回测执行 | 无决策，仅触发确定性计算 | read_file, write_artifact, run_backtest | — | 假设卡 → 回测报告 |
| VerificationAgent 独立核验员 | 对候选策略**证伪**，一票否决 | read_file, write_artifact, run_backtest, run_lookahead_lint, compare_baseline | lookahead-lint | 假设卡+回测报告 → 核验报告（ACCEPT/REJECT+反馈） |
| RegistryAgent 版本治理 | 登记候选、执行采纳/拒绝/回滚 | （确定性代码，无 LLM） | — | 核验结论 → 注册表记录 |
| PortfolioAgent 组合员 | 生效信号 → 目标持仓 → 调仓指令 | read_file, write_artifact, read_approved_version, write_orders_dryrun | qubo-portfolio | 生效版本+行情 → dry-run 指令 CSV |
| MonitorAgent 监测员 | 判断是否触发迭代 | read_file, write_artifact, run_backtest, trigger_iteration | — | 生效版本+最新行情 → 监测报告 |
| **Harness 控制器** | **不做任何策略判断**：调度、检查点、重试、闸门、预算、停止规则 | 全部执行权 | — | 见 2.2 |

**分工理由**：研究（生成）与核验（证伪）必须分离，否则同一智能体既当运动员又当裁判；组合员与监测员只读 approved 版本，保证"未批准的策略永远影响不了持仓"；一切数值计算（回测、指标、显著性、lint）由 `src/quantforge/compute/` 确定性代码完成，LLM 只做假设生成与结论汇总，消除"模型心算"带来的不可复现性。

**决策与执行控制分离**：智能体只返回判断与产物（报告、假设卡）；是否进入下一阶段、是否重试、是否暂停等闸、何时停止，全部由 Harness 依据 `config/workflow.yaml` 与 `config/limits.yaml` 执行。智能体无法绕过检查点自行推进流程。

### 2.2 长程运行

- **持久化状态与检查点**：SQLite（`state/quantforge.db`）。幂等键 = `run_id:stage_id:round`，阶段完成即写检查点；事件日志 append-only；审批记录、kv 状态（当前持仓、上次优化时的市场状态、连续失败计数）同库持久化。
- **依赖关系**：阶段 DAG 定义于 `config/workflow.yaml`，Harness 按拓扑序执行；数据不合格（FAIL）直接拦截在 data_check。
- **触发机制**：定时（每日 16:30 / 每周六 10:00，见 workflow.yaml triggers）+ 事件触发（监测发现衰减 → 立即启动迭代循环）。
- **重试策略**：阶段级失败指数退避重试，最多 3 次；超限记 `stage_failed_final` 并中止本次运行（检查点保留）。
- **停止与升级规则**：连续 3 次迭代核验失败 → 写 `ESCALATION.md` 并停止自动迭代，等待人工介入；LLM 调用次数/token 预算超限、阶段墙钟超时同理中止。
- **中断恢复**：进程被杀后，以同一 run_id 重启 `run_iteration`——已完成的阶段经幂等键检查直接跳过（事件日志记 `skip_checkpoint`），未完成阶段继续执行；人工闸门挂起后批准，恢复时不再重复询问（批准记录持久化）。证据见 `evidence/recovery_demo.md` 与 `tests/test_resume.py`。
- **限制**：单阶段 600 秒、单运行 30 次 LLM 调用 / 3 万 token、工具最小权限矩阵（组合员无法回测、研究员无法审批）。

### 2.3 核验与改进

- **独立核验**：VerificationAgent 与 ResearchAgent 上下文隔离（研究员的 rationale 不进核验输入，核验只看数值证据）；核验报告一经写出不可修改，重验产生新编号报告。
- **人工批准**：两个重大操作闸门——策略晋级（promote_strategy）、生成调仓指令（generate_orders）。非演示模式下写 `approval_request_*.md` 并挂起（`AwaitingApproval`），人工批准后恢复；批准/拒绝全部入库留痕。
- **有界修订循环**：REJECT → 反馈回研究员修订 → 重走回测核验，最多 3 轮；超限登记拒绝并结束本次迭代。
- **固定基线测试**：基线 v0 冻结不可变；候选与基线在**同一组 walk-forward 样本外窗口**上对比；采纳标准 = 经济显著（Calmar>0.05 且 Sharpe>0）+ 统计无害（日均收益差 bootstrap CI 下界 > -2bp）+ 回撤不恶化。
- **版本与回滚**：注册表记录 parent、状态迁移（candidate→approved/rejected→retired）与决策理由；`rollback(to_version)` 把生效指针退回任一历史版本并留痕。
- **防未来信息泄露**：三道检测——静态源码扫描（禁未来引用写法）、动态截断复算（缺失模式+数值一致性，20 个随机截断日）、持仓滞后检查（首日收益必为零）；walk-forward 带 embargo；信息截止时点写入数据质量报告。证据见 `tests/test_lookahead.py`（含故意注入泄露被抓获的反向用例）。

### 2.4 安全与合规

仓库不含任何凭据、密钥、客户隐私数据。LLM 密钥仅经环境变量注入（`LLM_API_KEY`）；未配置时自动降级为确定性 MockLLM，全部演示与测试离线可复现。

---

## 3. 文件导览

见根目录 `README.md`。

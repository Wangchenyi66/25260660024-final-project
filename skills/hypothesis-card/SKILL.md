---
name: hypothesis-card
description: 生成结构化的策略迭代假设卡。当策略研究员需要提出迭代假设、或根据核验反馈进行有界修订时使用。
---

# 假设卡技能

## 何时使用
- research_agent 每轮迭代必须产出假设卡；
- 核验 REJECT 后的修订轮：针对 `feedback_for_research` 修改，并在 `revision_note` 中说明改动点。

## 步骤
1. 读取 monitor_report 与当前生效版本参数，确定迭代动机（必须是数字证据）。
2. 只写 `params_patch`（增量），不写全量参数；由 harness 合并并校验边界。
3. 自检清单：
   - [ ] rationale 引用了 monitor_report 中的具体数字；
   - [ ] 所有参数在允许边界内；
   - [ ] parent_version 是注册表中最新 approved 版本；
   - [ ] 修订轮次数 ≤ workflow.yaml 的 max_rounds。
4. 写出 YAML 到 artifacts/hypothesis_card.yaml。

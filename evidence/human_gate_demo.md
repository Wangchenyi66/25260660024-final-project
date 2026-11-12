# 人工批准闸门证据

非自动模式下，两个重大操作（策略晋级、生成指令）必须人工批准；
挂起期间检查点保留，批准后恢复且不再重复询问。

## 审批留痕（state/quantforge.db approvals 表）
```
[('promote_strategy', 'approve', '演示审批人'), ('generate_orders', 'approve', '演示审批人')]
```

非演示环境请使用 `Harness(auto_approve=False)`，审批人会收到
`artifacts/approval_request_*.md` 并据此在系统中签字。

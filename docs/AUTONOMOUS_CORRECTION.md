# 自主电路纠错 / Autonomous Circuit Correction

`autonomous_correct_design` 将诊断、模型提案、真实仿真、指标验收和候选选择连接成一个
有界闭环。它适用于所有能够表示为 `CircuitDesign`、能够由当前 SPICE 适配器编译且具有
可测验收指标的电路。它不承诺对数学意义上的“任意电路”必然找到修复。

## 安全边界

1. 模型只能通过 `DesignPatch` 提案，不能直接写文件或控制 Multisim。
2. 补丁支持改值、改模型、改引脚连接、网络增删以及元件增删/替换，最多 64 个原子操作。
3. 每个候选先执行 compare-and-swap 校验、结构化重建和 SPICE 编译，再运行真实实验。
4. 默认只接受严格减少失败/未验证指标的候选；全通过后停止。
5. 多轮修改最终合并为一份相对原始 revision 的可逆补丁。
6. 最终补丁不会自动写回，仍需既有一次性审批和事务化应用流程。

模型规划过程只保存 provider、轮数、工具调用数和 token 用量等摘要；不保存完整模型
对话，也不保存 API Key。每个候选保留补丁、候选设计、实验、诊断和嵌套 manifest。

## CLI

纠错使用现有模型 Provider 配置；未传 `--config-path` 时读取 Windows 的
`%LOCALAPPDATA%\multisim-mcp\providers.json`（也可用
`MULTISIM_MCP_PROVIDER_CONFIG` 覆盖）。配置只引用 API Key 环境变量，命令和规范中都不
接收明文密钥。配置方法见 [`模型 Provider 配置`](MODEL_PROVIDER_CONFIGURATION.md)。

最小纠错规范示例：

```json
{
  "schema_version": 1,
  "title": "自动修复分压器",
  "commands": "op",
  "requirements": [
    {
      "id": "vout", "metric": "mean", "signal": "V(out)",
      "operator": "approximately", "target": 6.667,
      "tolerance_percent": 2, "unit": "V"
    }
  ],
  "theoretical_values": {"vout": 6.667},
  "objectives": [
    {"requirement_id": "vout", "goal": "target", "target": 6.667}
  ],
  "max_rounds": 4,
  "max_candidates_per_round": 4,
  "require_strict_improvement": true,
  "stop_on_first_pass": false
}
```

```powershell
multisim-mcp autonomous-correct-design `
  --design .\design.json `
  --spec .\autonomous-correction.json `
  --output .\correction-run `
  --provider deepseek `
  --json
```

MCP 客户端需要跨进程存活、取消、查询进度或在服务重启后续跑时，应使用
`submit_autonomous_correction`。作业会保存不含密钥的 Provider 配置、验证完成轮次的
实验 manifest，并从最后一个可信轮次继续；未完成轮次会在新的 attempt 目录中重做。
为避免模型请求期间被误判为失联，`heartbeat_timeout` 必须大于 `model_timeout`。

规范包含实验命令、硬性 `requirements`、可选多目标 `objectives`，以及最多 8 轮、每轮
最多 8 个候选的限制。一次运行最多执行 65 次实验。`success=true` 只表示当前内存候选已
取得有限实测的全通过证据，不表示补丁已经获得持久化授权。

## English summary

`autonomous_correct_design` runs a bounded diagnose-propose-simulate-select loop.
Model proposals may contain value and topology edits, but every candidate must
pass strict patch validation, structured SPICE compilation, and a real verified
experiment. Only a strict improvement can become the next in-memory revision.
An all-pass final design is consolidated into one reversible patch against the
original revision and still requires a separate explicit approval to persist.
Use `submit_autonomous_correction` for a durable, cancellable job with verified
round-level recovery; model credentials are never persisted in job state.

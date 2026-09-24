# v1.3.0-preview.1

这是第一阶段 AI Agent 插件预览版，重点是 Multisim 原生验证闭环和跨 Agent MCP 接入。

## 已包含

- RC、RLC、受限理想 OPAMP5 的自然语言 plan/run 工作流；
- Multisim 原生 OP/AC/TRAN、原生网表拓扑验收和报告证据；
- 持久后台工程任务、阶段、超时和取消；
- 组件版本映射清单和 14.3 已验证组件记录；
- 多板分区、跨板网络、连接器引脚和候选评分规划；
- 64 位 Agent + 32 位 Multisim COM Worker 架构；
- Qwen、ChatGPT、ClawCode、Workbody、DeepSeek 的通用 MCP 配置生成器。

## 已知边界

- 尚不是任意复杂电路的一句话生成器；
- 当前真实组件清单主要覆盖 Multisim 14.3；
- 未验证模型会失败关闭；
- 多板功能当前输出规划和评分，尚未自动生成多个独立 `.ms14` 工程；
- 本预览不包含独立桌面前端。

## 验证

本地完整测试：732 passed，8 skipped。发布前仍需在目标机器用已授权 Multisim
运行 `runtime_status` 和一个真实 RC 示例。

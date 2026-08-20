# 只读 EDA 模型诊断 / Read-only EDA Model Diagnosis

`multisim-mcp model-diagnose` 把一个固定、已验证的电路设计交给有界模型工具循环，
用于结构梳理、连接检查和报告建议。该入口与普通 `model` 命令分离：只有显式调用
`model-diagnose` 并指定设计输入时，四个只读 EDA 工具才会出现。

## 快速使用

Provider 配置完成后，可以直接分析安全 SPICE 网表：

```powershell
multisim-mcp model-diagnose `
  --input .\diagnosis-prompt.txt `
  --netlist .\circuit.cir `
  --provider deepseek `
  --max-rounds 8 `
  --max-tool-calls 16 `
  --json
```

也可以输入版本化 `CircuitDesign` JSON：

```powershell
Get-Content -Raw -Encoding utf8 .\diagnosis-prompt.txt |
  multisim-mcp model-diagnose `
    --stdin `
    --design .\design.json `
    --json
```

`--design` 与 `--netlist` 必须且只能选择一个。提示词仍只允许来自显式 stdin 或
UTF-8 文件，没有内联 `--prompt` 参数。

## 固定的四个工具

| 工具 | 作用 | 明确不做的事 |
| --- | --- | --- |
| `eda_get_design_summary` | 返回设计标识、版本、元件/网络/连接数量和有界类型统计 | 不返回网表正文、注释或路径 |
| `eda_list_components` | 按偏移量分页列出最多 20 个元件，可按类型过滤 | 不返回任意文件、完整 annotations/parameters |
| `eda_inspect_net` | 检查一个精确网络名的元件引脚连接，最多返回 100 项 | 不启动 Multisim、不测量电压 |
| `eda_run_structural_checks` | 检查未使用/单连接网络、参考地和模型溯源元数据 | 不执行仿真、ERC 或正确性证明 |

工具绑定捕获一个不可变 `CircuitDesign`，参数中没有文件路径、命令、后端名称或写入
目标。普通 `multisim-mcp model` 仍然不公开任何工具。

## 输入门禁

- `CircuitDesign` JSON 最大 8 MiB，要求 UTF-8、唯一字段名、有限数字、严格
  `schema_version=1`，未知字段会被拒绝；
- SPICE 文件最大 8 MiB 且正文不超过 4,000,000 字符；它只经现有安全解析器转换，
  不会执行；
- `.include`、`.lib`、`.control`、shell、文件读写等危险记录在联系模型前失败；
- 原始 `source_netlist`、设计 annotations 和模型来源路径不会进入工具结果；
- 单页元件数、单元件节点数、网络连接数、诊断数和总工具结果大小都有固定上限。

## 数据与隐私边界

该命令会向显式选择的 Provider 发送提示词、系统约束、四个工具 schema，以及模型按需
请求的有界工具结果。虽然原始网表正文不会直接发送，但元件名称、数值、模型名、网络
名和拓扑连接仍可能通过工具结果离开本机；模型也可以在工具调用上限内请求多个分页。
因此不要把“`source_netlist_exposed=false`”理解为零数据外传。分析专有设计前，应确认
Provider 的数据政策，或选择本地 Ollama。

完整 transcript 不写入 CLI JSON，只返回最终响应、轮次、调用数、Provider 序列和
用量汇总。Provider 仍可能按其自身政策记录请求。

## 成本、失败回退与取消

一次诊断可能包含多个模型轮次。默认最多 8 轮和 16 次工具调用，硬上限分别是 16 和
64。每一轮都可能产生 token 用量。Provider fallback 仍需同时给出顺序与显式授权：

```powershell
multisim-mcp model-diagnose `
  --input .\prompt.txt `
  --netlist .\circuit.cir `
  --provider deepseek `
  --fallback local-ollama `
  --allow-failover
```

只有网络错误、HTTP 408/409/429 或 5xx 才会进入 fallback。同一 Provider 不自动重试；
网络失败发生在服务端已经处理请求之后时，fallback 仍可能造成重复计费。Ctrl+C 会在
模型与工具边界取消调用；四个只读 handler 也会在长遍历中检查取消令牌。

## 库级使用

应用层可以自行构建绑定，而不使用 CLI：

```python
from multisim_mcp.agent_runtime import BoundedToolLoop
from multisim_mcp.eda_agent_tools import create_readonly_eda_bindings

bindings = create_readonly_eda_bindings(design)
loop = BoundedToolLoop(registry, bindings, max_rounds=8, max_tool_calls=16)
```

工厂不接受 `EdaBackend`，因此不会因为注册了 Multisim 后端就隐式启动 COM。以后增加
后端诊断时，也应通过独立白名单和显式授权接入。

## 当前限制

结构性诊断只能指出值得检查的拓扑特征，不能确认电路是否满足增益、频率、失真、稳定
性或功耗要求。下一阶段应把已有实验结果和验收指标作为只读证据接入，然后才考虑带
预览、审批、事务和回滚的 `DesignPatch` 纠错动作。

## English summary

`model-diagnose` explicitly enables four bounded, read-only tools over one
validated `CircuitDesign`. It accepts either strict JSON or a safe SPICE file,
rejects unsafe directives before contacting a provider, never exposes raw
source-netlist text, and cannot access files, run Multisim, simulate, or mutate
the design. Structured component and topology data requested by the model is
still sent to the selected provider, so proprietary designs require an
appropriate provider policy or a local model. Structural checks are not ERC,
simulation, or proof of electrical correctness.

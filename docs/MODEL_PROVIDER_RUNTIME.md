# 模型 Provider 运行时 / Model Provider Runtime

Multisim MCP 现在提供第一版传输无关模型运行时。它位于未来本地工作台的编排层，
不进入 MCP server、COM worker 或 EDA Core。当前支持 DeepSeek、OpenAI、Ollama
以及配置为 OpenAI-compatible 的服务，使用非流式 Chat Completions 兼容子集。

Provider 配置、环境变量和密钥存储规则见
[`MODEL_PROVIDER_CONFIGURATION.md`](MODEL_PROVIDER_CONFIGURATION.md)。

## 单次安全调用

先完成 Provider 配置并确认 `--probe` 通过。提示词只允许从显式 stdin 或 UTF-8
文件读取；CLI 故意不提供 `--prompt "..."`，避免提示词进入 shell 历史和进程列表。

```powershell
# UTF-8 文件输入；不暴露任何工具
multisim-mcp model --input .\prompt.txt --json

# 显式 stdin
Get-Content -Raw -Encoding utf8 .\prompt.txt |
  multisim-mcp model --stdin --provider deepseek

# 独立 system message 文件
multisim-mcp model --input .\prompt.txt `
  --system-file .\system.txt `
  --max-tokens 2000 `
  --temperature 0.2 `
  --timeout 60 `
  --json
```

`model` 命令是明确的网络和模型调用动作。它只进行一次请求，不公开 MCP/EDA 工具；
如果服务异常返回工具调用，命令会失败关闭。

## Provider 选择与失败回退

省略 `--provider` 时使用配置中的 `active_provider`。失败回退需要同时指定顺序和
双重授权：

```powershell
multisim-mcp model --input .\prompt.txt `
  --provider deepseek `
  --fallback local-ollama `
  --allow-failover `
  --json
```

运行时不会自动重试同一 Provider。只有网络错误、HTTP 408/409/429 或 5xx 才能进入
显式 fallback；认证、权限、请求错误、协议错误和取消不会回退。网络中断发生在服务端
已经处理请求之后时，fallback 仍可能产生第二次计费，因此默认关闭。

## 稳定对象

`multisim_mcp.model_provider` 提供：

- `ModelMessage`：严格的 system/user/assistant/tool 消息；
- `ToolDefinition` 与 `ToolCall`：有界函数 schema 和对象参数；
- `ModelUsage` 与 `ModelResponse`：规范化 token 用量、结束原因和请求编号；
- `ModelProvider` protocol：模型无关调用接口；
- `OpenAICompatibleProvider`：DeepSeek/OpenAI/Ollama 共享实现；
- `ModelProviderRegistry`：活动 Provider、显式选择和受限 fallback。

密钥在每次请求开始时从配置引用的环境变量重新读取，因此支持进程内密钥轮换。请求、
响应和工具参数都执行 UTF-8、有限 JSON、数量和大小验证。模型响应中的 API Key 会在
错误进入异常或 CLI JSON 前被替换为 `[REDACTED]`。

## 有界工具循环

`multisim_mcp.agent_runtime.BoundedToolLoop` 是库级能力，不会自动暴露 MCP 工具。
每个工具必须显式绑定三个对象：

1. `ToolDefinition`：发给模型的 JSON Schema；
2. `validate_arguments`：本地独立参数验证器；
3. `handler`：通过验证后执行的本地函数。

```python
from multisim_mcp.agent_runtime import BoundedToolLoop, ToolBinding
from multisim_mcp.model_provider import (
    ModelMessage,
    ModelProviderRegistry,
    ToolDefinition,
)
from multisim_mcp.provider_config import read_provider_config


def validate_measure(arguments):
    if set(arguments) != {"net"} or not isinstance(arguments["net"], str):
        raise ValueError("invalid net")
    return {"net": arguments["net"]}


def measure(arguments, cancel_event):
    # 示例仅返回确定性数据；真实实现应调用受约束的应用服务。
    return {"net": arguments["net"], "volts": 5.0}


definition = ToolDefinition(
    "measure",
    "Measure one named circuit net.",
    {
        "type": "object",
        "properties": {"net": {"type": "string"}},
        "required": ["net"],
        "additionalProperties": False,
    },
)
registry = ModelProviderRegistry.from_config(read_provider_config())
loop = BoundedToolLoop(
    registry,
    [ToolBinding(definition, validate_measure, measure)],
    max_rounds=8,
    max_tool_calls=16,
)
result = loop.run([ModelMessage("user", "Measure the output node")])
```

同一模型响应中的所有工具参数会先全部通过本地验证，之后才执行第一个 handler；未知
工具、重复调用 ID、未配对的历史工具消息和超限调用都会在执行前失败。多个 handler
之间不是自动事务，涉及写入的绑定仍应通过 `ExperimentApplicationService` 等事务
边界执行，不能把多个裸文件/COM 写操作直接组合为一个模型批次。

## 固定上限

| 边界 | 上限 |
| --- | ---: |
| 请求正文 | 1 MiB |
| 响应正文 | 2 MiB |
| 单条消息 | 262,144 字符 |
| 单次消息数量 | 256 |
| 工具定义数量 | 128 |
| 单个工具 schema / 参数 | 64 KiB |
| 单个工具结果 | 256 KiB |
| Agent 模型轮次 | 最大 16，默认 8 |
| Agent 工具调用 | 最大 64，默认 16 |
| 请求超时 | 0.1–300 秒 |

取消令牌会在调用、工具验证和工具执行边界检查。带取消令牌的 HTTP 调用在独立守护
I/O 线程执行，调用方会及时收到 `ModelCancelled`；底层连接会被尽力 shutdown，残余
I/O 最迟受原请求超时约束且不会阻止进程退出。工具 handler 自身必须协作检查传入的
`cancel_event`。

## 只读 EDA 诊断入口

`multisim-mcp model-diagnose` 现在是第一个正式工具化入口。它必须接收一个严格
`CircuitDesign` JSON 或安全 SPICE 网表，随后只公开设计摘要、分页元件、单网络连接
和结构性检查四个固定工具。普通 `model` 命令继续保持无工具。

```powershell
multisim-mcp model-diagnose `
  --input .\prompt.txt `
  --netlist .\circuit.cir `
  --max-rounds 8 `
  --max-tool-calls 16 `
  --json
```

该入口不会启动 Multisim、执行网表、运行仿真或修改设计；原始网表、annotations 和
文件路径不会进入工具结果。结构化元件/网络数据仍会按模型请求发送到所选 Provider，
详细隐私边界、输入门禁与输出上限见
[`READ_ONLY_EDA_DIAGNOSIS.md`](READ_ONLY_EDA_DIAGNOSIS.md)。

## 当前边界

本阶段尚未提供流式输出、持久会话、图形界面、token/费用预算策略、实验结果只读工具
或后端诊断。下一阶段应先把已有实验/验收证据接入只读分析，再提供带预览、事务、审批
和回滚的纠错/优化动作；不得把全部 MCP 工具无审查地复制到模型循环。

## English summary

The first transport-neutral runtime supports bounded, non-streaming
OpenAI-compatible Chat Completions, normalized messages/tool calls/usage,
per-request environment credential resolution, cooperative cancellation, and
explicit retryable-only provider failover. The CLI reads prompts only from
explicit stdin or UTF-8 files and exposes no tools. `model-diagnose` is a
separate explicit entry point with four fixed read-only bindings over strict
CircuitDesign JSON or safely parsed SPICE. It never exposes raw netlist text or
runs a backend, although requested structured circuit data is sent to the
selected provider. Streaming, persistence, transactional actions, UI, and
budget policy remain future workbench milestones.

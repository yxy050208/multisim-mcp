# DeepSeek 与 DeepSeek Harness 适配

本文说明 Multisim MCP 如何接入 DeepSeek 模型和 DeepSeek 官方 Harness，
并记录当前兼容边界。DeepSeek API 和 DeepSeek Harness 是两层不同能力：

- DeepSeek API 提供模型、推理和函数工具调用；
- DeepSeek Harness (`dsh`) 提供代理循环、工具、会话、审批和 Web UI；
- Multisim MCP 继续作为本地工程工具服务器，不保存模型 API Key。

## 当前兼容基线

本说明核对日期为 2026-08-18。DeepSeek Harness 官方仓库仍标记为
Developer Preview，并明确提示可能发生破坏性兼容变更。当前公开的
`@deepseek-ai/dsh-mcp-client` 包版本为 `0.1.0-rc.7`。

已确认的 MCP Client 行为：

- 支持 `stdio` 和 `streamable-http`；
- 外部工具注册为 `mcp__<serverName>__<rawName>`；
- 支持工具列表变化、断线重连、超时和完整世代替换；
- 当前只桥接 MCP Tools；Resources 和 Prompts 尚无 Harness 消费接口；
- 工具结果保留结构化内容，但较大的二进制产物不应直接进入模型上下文。

因此目前可直接使用 Multisim MCP 的工具层；实验 Resource 已有 Tool 等价入口，
五个 MCP Prompt 也已经通过 Harness Skill Bundle 提供等价工作流。

上游资料：

- <https://github.com/deepseek-ai/deepseek-harness>
- <https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.zh.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.zh.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.zh.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.zh.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workflow.zh.md>
- <https://api-docs.deepseek.com/api/create-chat-completion/>

## 生成 Harness 配置片段

使用安装了 `multisim-mcp` 的 32 位 Python 运行：

```powershell
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client deepseek-harness `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack `
  --work-dir C:\msre_exp `
  --artifact-export-dir C:\MultisimMcp\exports `
  --tool-profile experiment
```

命令输出一个 Cordis 插件配置片段，不会修改 Harness 的现有 profile，
也不会读取或输出 `DEEPSEEK_API_KEY`。把片段加入所选 profile 的 Cordis
配置层后，可用以下命令检查最终组合：

```powershell
dsh --profile web --dump-config
```

片段形状：

```yaml
- id: "mcp-multisim"
  name: "@deepseek-ai/dsh-mcp-client"
  config:
    serverName: "multisim"
    transport: "stdio"
    command: "C:\\path\\to\\python32\\python.exe"
    args:
      - "-m"
      - "multisim_mcp.server"
    env:
      MULTISIM_MCP_ARTIFACT_EXPORT_DIR: "C:\\MultisimMcp\\exports"
      MULTISIM_MCP_TEMPLATE_DIR: "C:\\MultisimMcp\\component-pack"
      MULTISIM_MCP_TOOL_PROFILE: "experiment"
      MULTISIM_MCP_WORKDIR: "C:\\msre_exp"
    failOnStartupError: true
    toolCallTimeoutMs: 120000
    reconnect:
      enabled: true
      initialDelayMs: 500
      maxDelayMs: 30000
      maxAttempts: 10
```

Harness 的 `serverName` 当前只接受 1--32 个字母、数字、下划线或连字符；
配置生成器会单独执行这个限制，不会生成上游无法加载的名称。

## DeepSeek API Key 边界

正确的凭据流向：

```text
DEEPSEEK_API_KEY -> DeepSeek Harness -> DeepSeek API
Multisim license -> local Multisim installation -> 32-bit worker
```

不要把 `DEEPSEEK_API_KEY` 放入 Multisim MCP 的 `env`、实验 manifest、报告
或诊断输出。Multisim MCP 不需要也不应该直接调用模型 API。

## 模型与工具规模

DeepSeek Chat Completions 当前允许最多 128 个函数工具，函数名最长 64 个
字符。Multisim MCP 1.0 的 51 个工具可以被加载，但完整 schema 会占用模型
上下文，也会降低工具选择稳定性。当前已经提供以下服务端 Tool Profile：

| Profile | 工具数 | 用途 |
| --- | ---: | --- |
| `core` | 26 | 诊断、连接、电路检查、文件和基础仿真 |
| `experiment` | 39 | 电路生成、实验任务、测量、验证、报告和产物访问 |
| `optimization` | 40 | 参数调整、基础仿真、指标验证、实验扫描和产物访问 |
| `full` | 55 | 完整兼容模式，也是默认值 |

通过配置生成器的 `--tool-profile` 设置，或手工设置
`MULTISIM_MCP_TOOL_PROFILE`。Profile 在服务端生成稳定的 `tools/list`；没有被
选择的工具不会注册到该进程，但原有 Python 内部调用保持不变。`runtime_status`
会返回当前 profile、工具数和可选 profile。

Tool Profile 是上下文与工作流优化机制，不是安全权限系统。任意命令、路径边界、
输出目录和危险功能仍由现有安全策略独立控制。

## Resources 和 Prompts 的兼容

Harness 当前不消费 MCP Resources 和 Prompts。现已提供四个 Tool 等价入口：

- `list_experiment_artifacts`：列出名称、URI、MIME、大小和 SHA-256；
- `read_experiment_artifact`：只读取有界文本或分页内容；
- `export_experiment_artifact`：把二进制产物复制到用户批准的位置；
- `get_experiment_summary`：汇总测量、验证和报告入口；

其中 `read_experiment_artifact` 只接受固定文本产物，并以字符偏移分页；二进制
产物不会以内联 base64 返回。`export_experiment_artifact` 只允许复制固定产物到
`MULTISIM_MCP_ARTIFACT_EXPORT_DIR` 下的相对子目录，默认拒绝覆盖。该目录必须由
用户或客户端配置明确批准。

## 安装 Harness Skill Bundle

在 Harness 项目根目录运行：

```powershell
multisim-mcp harness-skills --output .dsh/skills
```

安装器会写入五个随 Python 包版本化发布的项目级 Skill：

- `/multisim-create-experiment`：根据需求创建并运行实验；
- `/multisim-debug-circuit`：诊断电路、证据和失败原因；
- `/multisim-compare-experiments`：比较两个实验及其产物；
- `/multisim-write-lab-report`：基于已记录证据编写实验报告；
- `/multisim-verify-requirements`：逐条验证工程指标。

Harness 会从项目的 `.dsh/skills` 自动发现这些 Skill。用户可以用上述斜杠名称
显式调用，模型也可以根据 Skill 描述自行加载。MCP 工具在 Harness 中通常显示为
`mcp__multisim__<tool-name>`；Skill 正文使用原始工具名描述步骤，避免把
`serverName` 硬编码进工作流。

安装器默认拒绝覆盖已有 `SKILL.md`。只有明确希望恢复打包版本时才使用
`--force`；自动化环境可增加 `--json` 获得稳定的安装结果。之所以采用 Skill
而不是动态 Workflow，是因为这五项能力是固定、可审查的实验说明。Harness 的
Workflow seam 更适合由模型生成编排脚本和子代理的动态任务，后续复杂优化流程
确有这种需求时再单独引入。

## 独立 Harness Bundle

仓库同时提供可独立安装的 bundle 源码：
[`integrations/deepseek-harness`](../integrations/deepseek-harness)。它按照官方
`dsh.bundle.patch` 约定声明 `cordis.patch.yml`，并把 MCP Client 依赖固定为
`0.1.0-rc.7`。

```powershell
$env:MULTISIM_MCP_PYTHON = "C:\path\to\python32\python.exe"
dsh plugin --profile web add .\integrations\deepseek-harness
dsh --profile web --dump-config
```

当前 bundle 可以从源码路径安装，但尚未发布到 npm。正式 npm 发布需要单独验证
包名所有权、Trusted Publishing 和固定 Harness 版本，不能与 Python 包发布隐式
绑定。配置只转发 `MULTISIM_MCP_*` 和 Python 运行选项，不会把
`DEEPSEEK_API_KEY` 传入 MCP 子进程。
首次发布、Registry 防抢注检查和后续 OIDC 暂存审批流程见
[`DeepSeek Harness 插件 npm 发布手册`](DEEPSEEK_HARNESS_NPM_RELEASE.md)。

## 官方 dsh 端到端烟雾测试

维护者可以运行：

```powershell
python tools/smoke_deepseek_harness.py --json
```

该脚本使用临时 `DSH_HOME`，删除子进程环境中的 `DEEPSEEK_API_KEY`，强制关闭
Harness 遥测，并固定运行 `@deepseek-ai/dsh@0.1.0-rc.7`。它先验证配置组合，再
启动 Web profile；由于 MCP 配置启用了 `failOnStartupError`，只有官方 MCP Client
成功连接、完成初始工具同步并且 Web 服务就绪才算通过。脚本不执行模型请求，也不
需要 DeepSeek API Key。

真实启动测试只在每周或手动 GitHub Workflow 中运行，避免 npm 首次下载时间和
上游注册表波动拖慢普通 PR。它与非阻塞的版本漂移检查不同：固定版真实启动失败会
让烟雾测试 job 失败，作为需要处理的兼容信号。

PDF、图片、raw 和 `.ms14` 默认只返回元数据与本地引用，不应把完整 base64
塞入模型历史。所有路径继续经过实验注册表和工作区边界检查。

## 验证矩阵

每个已支持的 Harness 基线应验证：

1. stdio initialize 和分页 `tools/list`；
2. `runtime_status` 和一个无 COM 工具调用；
3. Windows 32 位环境中的持久实验提交与状态查询；
4. 工具名不超过 DeepSeek 函数命名限制；
5. 工具参数 schema 能被 Harness 接受；
6. 子进程崩溃后的重连不会重复注册工具；
7. stdout 没有日志、编码或 pywin32 诊断污染；
8. 配置和产物中不存在 DeepSeek API Key。

固定版本测试负责发布门禁；跟踪 Harness 最新版本的定时任务只报告兼容
状态，不应在上游发生破坏性变更时阻塞 Multisim MCP 的普通提交。

## 版本门禁与上游监控

仓库使用 [`compatibility/deepseek-harness.json`](../compatibility/deepseek-harness.json)
记录已验证基线。当前固定值包括 Harness 与 MCP Client `0.1.0-rc.7`、Node
`^22.19.0 || >=24.0.0`、pnpm `11.7.0` 和上游 MCP SDK `^1.12.0`。

维护者可以执行确定性的本地契约检查；该命令不访问网络：

```powershell
python tools/check_deepseek_harness_compat.py --json
```

准备发布或调整固定版本时，执行严格上游检查：

```powershell
python tools/check_deepseek_harness_compat.py --check-upstream --json
```

每周 GitHub Actions 使用 `--warn-only` 比较官方仓库。版本、Node 范围、包管理器、
MCP Client 名称或 MCP SDK 范围发生变化时，它会写入 Job Summary，但不阻塞普通
开发。出现漂移不等于已经不兼容；维护者需要先运行 stdio、工具 schema、Skill
发现和 Windows 32 位实验矩阵，再更新固定值与核对日期。

## 独立平台中的 DeepSeek

第一版通用 `ModelProvider` 已通过 OpenAI-compatible Chat Completions 接入
DeepSeek，负责严格消息、用量、取消、显式回退和有界工具循环，不负责电路或仿真
逻辑。当前 CLI 不公开工具，库级工具循环也要求逐项白名单与独立参数验证；后续由
可视化工作台绑定受约束的 EDA 应用服务。同一 EDA Core 仍必须可以由 DeepSeek、
其他模型或完全无模型的确定性脚本调用。详见
[`MODEL_PROVIDER_RUNTIME.md`](MODEL_PROVIDER_RUNTIME.md)。

## English summary

DeepSeek is a model provider; DeepSeek Harness is an agent runtime. The initial
integration generates a version-gated Cordis MCP client fragment and keeps all
model credentials outside Multisim MCP. The current Harness bridge consumes MCP
tools but not resources or prompts. Bounded artifact tools, tool profiles, and a
five-skill project bundle now provide equivalent access. Pinned compatibility
checks now cover the local contract, while real Windows Harness
execution remains part of the release matrix. A separate bounded provider
runtime now supports direct DeepSeek calls without coupling model formats to the
EDA core.

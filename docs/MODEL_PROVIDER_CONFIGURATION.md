# 模型 Provider 自助配置 / Model Provider Self-Configuration

本文说明 Multisim MCP 本地工作台提供的第一版模型 Provider 配置能力。
它解决的是“发现、保存和诊断模型 API 配置”，还不是内置的模型对话或 Agent
运行时。Multisim MCP 本身不需要模型密钥，密钥不会传入 MCP、COM worker、实验
manifest、报告或导出产物。

## 安全模型

- 配置文件只保存环境变量名，例如 `DEEPSEEK_API_KEY`，不保存变量值；
- `configure` 默认只预览，不写文件，也不联网；
- 只有 `--apply` 会原子写入，已有 Provider 默认合并保留；
- 只有 `--probe` 会连接 Provider，并且只调用配置的模型列表端点；
- 带用户名、密码、查询参数或片段的 Base URL 会被拒绝；
- 非回环地址必须使用 HTTPS，本机 `localhost`、`127.0.0.1` 和 `::1` 可使用 HTTP；
- JSON 输出和错误信息不会包含 API Key 值。

Windows 默认配置文件为
`%LOCALAPPDATA%\multisim-mcp\providers.json`。可用
`MULTISIM_MODEL_PROVIDER_CONFIG` 或 CLI 的 `--path` 选择其他位置。

## 快速开始：DeepSeek

先在当前用户会话中设置密钥。以下值只是占位符，不要把真实密钥提交到仓库：

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'

# 1. 自动发现并预览；不会写文件
multisim-mcp configure --auto --json

# 2. 确认后写入环境变量引用；不会写入密钥值
multisim-mcp configure --auto --apply

# 3. 脱敏查看
multisim-mcp configure --show --json

# 4. 显式进行连接与模型可用性诊断
multisim-mcp configure --show --probe deepseek --json
```

内置 DeepSeek preset 使用 OpenAI-compatible Base URL
`https://api.deepseek.com`。当前默认模型是 `deepseek-v4-flash`；也可显式设置：

```powershell
$env:DEEPSEEK_MODEL = 'deepseek-v4-pro'
multisim-mcp configure --provider deepseek `
  --model deepseek-v4-pro `
  --apply
```

模型名可能随上游变化。发布版内置值以代码发布时的 DeepSeek 官方文档为准，长期
部署建议通过 `DEEPSEEK_MODEL` 或 `--model` 明确固定。

## 支持的自动发现变量

| Provider | 必需变量 | 可选变量 | 说明 |
| --- | --- | --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL` | 有安全默认模型和 URL |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | `OPENAI_BASE_URL` | 不猜测模型名 |
| Ollama | `OLLAMA_MODEL` | `OLLAMA_BASE_URL` | 默认本机 OpenAI-compatible URL，无密钥 |
| OpenAI-compatible | `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_MODEL` | `OPENAI_COMPATIBLE_API_KEY` | 自定义服务 |

自动发现只返回配置完整的 Provider。比如发现 `OPENAI_API_KEY` 但没有
`OPENAI_MODEL` 时，会在 `skipped` 中给出缺失变量，而不会猜测或写入半成品配置。

## 手动配置

```powershell
# OpenAI：密钥仍只从 OPENAI_API_KEY 读取
multisim-mcp configure --provider openai `
  --model '<model-id>' `
  --apply

# 本地 Ollama
multisim-mcp configure --provider ollama `
  --model 'qwen3:8b' `
  --apply --probe

# 任意 OpenAI-compatible HTTPS 服务
$env:LAB_MODEL_KEY = '<your-key>'
multisim-mcp configure --provider openai-compatible `
  --name lab-model `
  --base-url 'https://models.example.com/v1' `
  --model 'lab-model-v1' `
  --api-key-env LAB_MODEL_KEY `
  --apply
```

默认 `--apply` 会按 Provider ID 更新或添加条目，并保留其他已有条目。只有明确希望
丢弃旧条目时才使用 `--apply --replace`。

## 工作台配置页

启动 `workbench-api` 和 React 工作台后，点击右上角齿轮图标进入“模型与 API 配置”
（英文界面为 `Model / API settings`）：

1. 页面从 `GET /api/provider-config` 读取已保存配置，或按当前进程环境变量做脱敏发现；
2. 在右侧填写 Provider 类型、配置 ID、Base URL、模型、`/models` 路径和 API key 环境变量名；
3. `Test connection` 才会显式调用本地回环 API 的 `POST /api/provider-probe`，服务端从
   环境变量读取密钥并只返回状态、HTTP 状态和模型是否可用；
4. `Copy CLI apply` 生成不含密钥的 `multisim-mcp configure ... --apply` 命令，执行命令后
   刷新页面即可看到已保存配置。

工作台 API 仍然只绑定 `127.0.0.1`/`localhost`/`::1`。页面没有配置写入按钮，也不会把
密钥值发送到浏览器；这使得前端可以作为本地操作面板使用，同时保留 CLI 的明确写入门槛。

## 配置格式

```json
{
  "schema_version": 1,
  "active_provider": "deepseek",
  "providers": {
    "deepseek": {
      "id": "deepseek",
      "provider": "deepseek",
      "api_format": "openai-compatible",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-v4-flash",
      "models_path": "/models",
      "credential": {
        "source": "environment",
        "name": "DEEPSEEK_API_KEY"
      }
    }
  }
}
```

读取时会重新验证 schema、URL、Provider ID、模型名和凭据引用。出现 `api_key`、
`token`、`secret` 等明文字段时会失败关闭。

## 当前边界与后续阶段

第一版通用 `ModelProvider` 运行时、单次安全 CLI 调用、用量规范化、取消、显式失败
回退和有界工具循环已经实现，详见
[`MODEL_PROVIDER_RUNTIME.md`](MODEL_PROVIDER_RUNTIME.md)。当前尚未把 EDA 应用服务
预绑定为模型工具；配置页目前不提供流式对话或会话持久化。Provider 运行时仍不得让模型凭据
进入 Multisim MCP server 或 COM worker。

## English summary

`multisim-mcp configure` discovers, previews, atomically stores, and probes
model-provider settings for the local workbench. The `Model / API settings` page reads
the same secret-free document, lets users draft a provider, explicitly probe
its models endpoint, and copy the CLI apply command. Configuration files
contain environment-variable references only, never credential values. Preview
is the default; `--apply` is required to write and `--probe` is required to make
a network request. Existing providers are merged unless `--replace` is explicit.
A separate bounded runtime now provides explicit chat calls and an allowlisted
library-level tool loop; model credentials are never forwarded to the Multisim
MCP server or its COM worker.

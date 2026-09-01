# Multisim MCP Core v1.2.0 候选版

这是一个**不含 React 前端**的 MCP 核心候选版本。它面向需要通过 Codex、DeepSeek
Harness 或其他 MCP Client 直接调用电路设计与实验能力的用户，同时为后续独立工业
软件保留稳定、可审计的服务边界。

> 当前文档描述 GitHub 候选源码。合并、CI、真实 Multisim 门禁、PyPI、GitHub Release
> 和 MCP Registry 发布完成前，不应把 `1.2.0` 当作正式稳定发行版。

## 核心能力

- 78 个 MCP 工具、20 个资源模板、5 个中英双语提示词。
- 从需求到候选方案、设计规格、网表草稿、元件解析和可执行网表的审批式设计链路。
- 结构诊断、补丁预演/事务、约束优化、全局优化、自主纠错和多方案 Pareto 比较。
- Multisim、ngspice、行为参考和差分验证后端；支持数字观测、SPICE 兼容性与来源证明。
- 持久实验/优化作业、严格目录清单、SHA-256 完整性校验、中断恢复和安全重试。
- DeepSeek、OpenAI、Ollama 与 OpenAI-compatible Provider 配置；密钥仅以环境变量引用，
  模型工具循环受到白名单、轮次、调用数和结果大小限制。
- 五个随 Python 包安装的 DeepSeek Harness Skills；独立 npm 插件继续使用 `1.1.0`，
  不随 Python 核心候选版自动发布。

## 不包含的内容

- React Workbench、桌面壳、一键启动器和面向终端用户的可视化交互页面。
- NI 软件、样例电路、解码 XML、`.ms14` 文件、本机模板包、实验结果和个人路径。
- 未经真实 Multisim 或明确后端证据验证的“工业级正确性”承诺。

Python 包仍可提供 loopback 桥接 API，供可信的本地客户端读取有界快照和提交经过审批的
工作流；这些 API 不是前端，也不是公网、多租户服务。

## 候选版验证门禁

```powershell
.\tools\verify_mcp_release.ps1
```

该脚本验证版本一致性、完整测试、MCP/DeepSeek Harness 合约、发布审计、wheel/sdist
内容以及 CLI 自检。真实 Multisim COM 回归仍需在安装并授权 NI 软件的 Windows 主机上
单独执行。

## English summary

This is a frontend-free MCP Core 1.2 release candidate. It packages the Python
MCP server, CLI, transport-neutral EDA core, approved design workflow, diagnosis,
optimization, autonomous correction, durable jobs, Multisim/ngspice backends,
model-provider integration, tests, and documentation. It deliberately excludes
the React Workbench and all NI/local artifacts. The candidate exposes 78 tools,
20 resource templates, and five bilingual prompts. It must not be presented as a
stable release until CI, real-runtime gates, packaging, PyPI, and registry
publication are complete.

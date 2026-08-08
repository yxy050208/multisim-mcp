# Multisim MCP v0.1.0-alpha.2

[PyPI](https://pypi.org/project/multisim-mcp/0.1.0a2/) ·
[官方 MCP Registry / Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[源代码 / Source](https://github.com/yxy050208/multisim-mcp/tree/v0.1.0-alpha.2)

## 中文（主要说明）

这个 Alpha 增量版本为 MCP 目录和客户端增加了诚实的跨平台工具发现能力：

- Linux/Docker 可以启动 MCP、完成初始化并列出工具；
- `runtime_status` 在容器中明确返回 `introspection-only`；
- `pywin32` 仅在 Windows 安装；
- 所有 Multisim COM 操作在不兼容平台上都会立即返回清晰错误；
- 新增非 root Glama 验证镜像、最小 Docker 构建上下文和 Ubuntu 容器握手 CI。

容器不包含 NI 软件、样例、许可证或本地提取模板，也不能执行 Multisim 仿真。
真实电路生成、实验和导出仍要求本地授权 Multisim 14+ 与 32 位 Windows Python。

Windows 安装：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==0.1.0a2"
```

## English summary

This incremental alpha adds an honest cross-platform discovery mode for MCP
registries and clients. The Linux container starts the real stdio server,
completes MCP initialization, and exposes tool schemas, while diagnostics label
the runtime as `introspection-only`. COM-backed operations continue to fail
closed unless the server runs under 32-bit Python on Windows with a licensed
local Multisim installation.

The validation image contains no NI software, samples, licenses, or extracted
templates and does not claim to run Multisim simulations.

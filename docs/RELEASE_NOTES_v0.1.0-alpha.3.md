# Multisim MCP v0.1.0-alpha.3

[PyPI](https://pypi.org/project/multisim-mcp/0.1.0a3/) ·
[官方 MCP Registry / Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[源代码 / Source](https://github.com/yxy050208/multisim-mcp/tree/v0.1.0-alpha.3)

## 中文（主要说明）

这个版本重点降低第一次安装和接入 Multisim MCP 的难度，同时保持现有 MCP
客户端配置完全兼容。

### 新增

- `multisim-mcp doctor`：默认无副作用检查 Windows、32 位 Python、pywin32、
  Multisim COM 注册、本地模板包和固定版本 `.ms14` 编解码器；支持稳定 JSON、
  中文/英文输出、`--strict` 和显式 `--connect` 真实激活验证。
- `multisim-mcp config`：生成 Claude Desktop JSON、Codex TOML 或通用 stdio
  配置片段；默认仅预览，写文件时拒绝静默覆盖。
- `multisim-mcp serve`：显式启动 stdio server；原有无参数启动方式保持不变。
- `python -m multisim_mcp`：与安装后的 console script 使用相同入口。

### 可靠性

- pywin32 生成包装缓存损坏时自动回退到动态 COM Dispatch，不删除用户缓存。
- 将 pywin32 首次导入信息转移到 stderr，避免污染 JSON 和 MCP stdout。
- 源码模式的 MCP stdio 测试显式使用实际导入的包路径，避免子进程丢失
  `PYTHONPATH`。

### 验证

- 63 项无 COM 测试全部通过；项目 Agent 工作流技能校验通过。
- code-only wheel 的 `doctor` JSON 和无参数 MCP stdio 握手通过，返回 33 个工具。
- 使用 32 位 Python 成功连接 Multisim 14.3。
- 真实分压器发布冒烟实验生成 `.ms14`、PNG、CSV、raw、SVG 和 Markdown 报告；
  11 个 DC 扫描点的 `V(vout) / VIN` 均为 `0.5`。

Windows 安装：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==0.1.0a3"
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --connect
```

## English summary

This alpha adds a backward-compatible CLI for side-effect-free diagnostics,
explicit real-COM probing, and preview-first Claude Desktop/Codex configuration
generation. It also recovers from corrupt pywin32 generated-wrapper caches by
falling back to dynamic Dispatch and keeps all JSON/MCP stdout protocol-clean.

Validation covers 63 COM-free tests, an isolated code-only wheel, a 33-tool MCP
stdio handshake, a real Multisim 14.3 connection, and a complete generated
resistor-divider experiment whose measured ratio is 0.5 across the DC sweep.

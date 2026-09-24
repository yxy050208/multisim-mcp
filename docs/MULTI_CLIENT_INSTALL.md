# 多 Agent 安装方式

所有客户端使用同一个 MCP Server 配置。客户端本身可以使用 64 位 Python；
Multisim COM Worker 必须使用 32 位 Python。通过 `MULTISIM_MCP_PYTHON` 或
`--python32` 指定 32 位解释器，不要把 64 位解释器直接用于 COM。

生成配置：

```powershell
python tools/generate_agent_config.py `
  --client qwen `
  --python32 C:\Python312-32\python.exe `
  --output .\generated-config\qwen.json
```

`--client` 可选：`qwen`、`chatgpt`、`clawcode`、`workbody`、`deepseek`。
生成文件采用通用 `mcpServers` 结构；不同宿主如果需要外层字段，只需复制
`mcpServers.multisim` 节点，不需要修改服务器参数。

安装前至少需要 Windows、已授权 Multisim、32 位 Python/pywin32 和本地模板包。
64 位 Agent 只负责协议和界面，所有 COM 操作由 32 位 Worker 隔离执行。

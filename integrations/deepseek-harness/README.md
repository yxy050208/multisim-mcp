# Multisim MCP 的 DeepSeek Harness 插件

这是一个可独立安装的 Harness bundle。它把 Multisim MCP 注册为
`mcp__multisim__*` 工具，并默认使用 `experiment` Tool Profile。

## 本地安装

先在 32 位 Python 中安装 `multisim-mcp`，然后设置该解释器路径：

```powershell
$env:MULTISIM_MCP_PYTHON = "C:\path\to\python32\python.exe"
dsh plugin --profile web add .\integrations\deepseek-harness
dsh --profile web --dump-config
```

在发布到 npm 后，可以把本地路径替换为
`multisim-mcp-dsh-plugin@<version>`。当前源码包尚未发布到 npm。

维护者发布前必须运行 Registry 与包边界守卫。首次发布需要本地 2FA；后续版本使用
OIDC 暂存并由维护者 2FA 审批。完整步骤见
[`docs/DEEPSEEK_HARNESS_NPM_RELEASE.md`](../../docs/DEEPSEEK_HARNESS_NPM_RELEASE.md)。

可选环境变量：

- `MULTISIM_MCP_TOOL_PROFILE`：`core`、`experiment`、`optimization` 或 `full`；
- `MULTISIM_MCP_TEMPLATE_DIR`：本地组件模板包；
- `MULTISIM_MCP_WORKDIR`：实验工作目录；
- `MULTISIM_MCP_ARTIFACT_EXPORT_DIR`：允许导出产物的根目录。

不要把 `DEEPSEEK_API_KEY` 放入 MCP 子进程配置。该凭据只属于 Harness。
MCP 服务器命令在 agent 沙箱之外执行，应当只安装可信发布物并固定版本。

## English summary

This package is an installable DeepSeek Harness bundle for Multisim MCP. Set
`MULTISIM_MCP_PYTHON` to the installed 32-bit Python interpreter before adding
the bundle to a Harness profile. Model credentials remain in Harness and are
never forwarded to the MCP child process. Maintainer release instructions are
in `docs/DEEPSEEK_HARNESS_NPM_RELEASE.md`.

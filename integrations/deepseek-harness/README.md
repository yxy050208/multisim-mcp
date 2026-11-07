# 1.3.0 npm bundle / 1.3.0 core compatibility

The npm bundle is `multisim-mcp-dsh-plugin@1.3.0`. The public Python
package is `multisim-mcp==1.3.0`. The bundle remains a thin adapter: it does not
include Python, Multisim, or local component templates. It uses the configured Python MCP
server and does not bundle Python or Multisim. New capabilities remain limited to the
documented validated circuit families.

# Multisim MCP 的 DeepSeek Harness 插件

这是一个可独立安装的 Harness bundle。它把 Multisim MCP 注册为
`mcp__multisim__*` 工具，并默认使用 `experiment` Tool Profile。

## 安装

先在 32 位 Python 中安装与 bundle 同版本的 `multisim-mcp`，设置该解释器路径，
再把固定版本的 bundle 加入 Harness profile：

```powershell
$env:MULTISIM_MCP_PYTHON = "C:\path\to\python32\python.exe"
& $env:MULTISIM_MCP_PYTHON -m pip install "multisim-mcp==1.3.0"
dsh plugin --profile web add "multisim-mcp-dsh-plugin@1.3.0"
dsh --profile web --dump-config
```

安装或升级 bundle 后应重新启动该 profile。发布前验证当前源码时，应先用
`npm pack .\integrations\deepseek-harness --ignore-scripts` 生成 `.tgz`，再把该
tarball 的绝对路径交给 `dsh plugin ... add`，以验证与最终发布相同的包边界。

### 安装陷阱（实测记录，务必逐条核对）

只执行 `dsh plugin ... add` 常常**不会**让 `mcp__multisim__*` 出现，而且
**不产生任何错误日志**——工具就是静默缺失。以下两条是硬性要求。

**① bundle 必须进入 `dsh.profile.bundles`。**
`pnpm add` / `dsh plugin add` 只会写进 `dependencies`，而 profile 目录下
`cordis.yml` 的规则是"由 `dsh.profile.bundles` 中每个 bundle 的 patch，再加
`cordis.patch.yml`"组成。只进 `dependencies` 的包，其 `cordis.patch.yml`
不会被执行。确认 `profiles/<profile>/package.json` 里有：

```json
"dsh": {
  "profile": {
    "bundles": ["...", "multisim-mcp-dsh-plugin"]
  }
}
```

**② `@deepseek-ai/dsh-mcp-client` 必须与宿主同版本。**
该包有 9 个 peer 依赖全部指向宿主核心包（`cordis`、`dsh-llm`、`dsh-scope`、
`dsh-tools`、`dsh-timeout`、`dsh-attachment`、`dsh-subprocess`、
`dsh-mcp-resources`、`dsh-system-prompt`），靠宿主路由解析。宿主版本由
`~/.dsh/dsh-runtimes/dsh-primary-runtime/runtime.json` 的 `desktopVersion`
给出；profile 中安装的 client 及 `pnpm-workspace.yaml` 里的 override 必须与之
一致，否则节点激活失败且依然无日志：

```powershell
(Get-Content "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\runtime.json" -Raw |
  ConvertFrom-Json).desktopVersion     # 例：0.2.1-rc.3
```

```yaml
# pnpm-workspace.yaml
overrides:
  '@deepseek-ai/dsh-mcp-client': 0.2.1-rc.3   # 与上面的 desktopVersion 相同
```

已验证的症状：宿主自动升级到 `0.2.1-rc.3` 而 override 仍固定 `0.2.1-rc.2` 时，
所有 `mcp__multisim__*` 变为 unknown tool；把 override 与实装版本同步到
`0.2.1-rc.3` 并重启 profile 后立即恢复（同 profile 下 92 个工具全部注册，进程链为
`DSH host → python -m multisim_mcp.server → com_worker`）。

因为宿主小版本会随 Harness 更新变化，升级 Harness 后请重新核对这两条。

维护者发布前必须运行 Registry 与包边界守卫。首次发布需要本地 2FA；后续版本使用
OIDC 暂存并由维护者 2FA 审批。完整步骤见
[`docs/DEEPSEEK_HARNESS_NPM_RELEASE.md`](../../docs/DEEPSEEK_HARNESS_NPM_RELEASE.md)。

可选环境变量：

- `MULTISIM_MCP_TOOL_PROFILE`：`core`、`experiment`、`optimization` 或 `full`；
  `experiment` profile 还包含 `build_behavioral_reference` 和
  `run_behavioral_reference`，用于明确的 DFF 行为级 ngspice 参考实验；
  使用 `optimize_design`、`global_optimize_design`、`autonomous_correct_design`、
  `submit_global_optimization`、`submit_autonomous_correction` 或
  `compare_design_variants` 时选择 `optimization`（或 `full`）；
  `evaluate_design_patch` 在 `experiment`、`optimization` 和 `full` 中可用；
  `diagnose_design` 在四种 profile 中均可用；
- `MULTISIM_MCP_TEMPLATE_DIR`：本地组件模板包；
- `MULTISIM_MCP_WORKDIR`：实验工作目录；
- `MULTISIM_MCP_ARTIFACT_EXPORT_DIR`：允许导出产物的根目录。
- 自然语言工程工具包括 RC、RLC 和 OPAMP 的 plan/run 入口；后台运行使用
  `submit_natural_engineering_job`，`kind` 为 `rc`、`rlc` 或 `opamp`。
- 后台任务通过 `get_experiment_job` 和 `multisim://jobs/{job_id}` 查询阶段、取消和证据。
- 未验证元件模型和未通过拓扑/数值验收的任务会失败关闭，不会仅因生成 `.ms14` 就报告成功。
- `MULTISIM_MCP_MODEL_ROOT`：`compile_executable_netlist` 重新哈希获批外部模型时唯一允许
  读取的本地根目录；模型 URI 必须是该目录内的相对路径。

`experiment` profile 现在包含 `compile_executable_netlist` 和
`approve_executable_netlist`。它们目前只对 `signal-passive` 生成并确认内存中的引脚级
`CircuitDesign` / SPICE 预览，其他方案失败关闭；批准凭证只开放成图准备，不会写文件、
添加激励、执行分析或启动仿真。要消费审批凭证，调用
`create_schematic_from_netlist` 时同时传入完整预览 `executable_netlist`、审批凭证
`netlist_approval` 和预览中原样的 `spice_netlist`；工具会在写入 `.ms14` 前重新校验绑定，
拒绝缺凭证或被修改的网表，且不会启动仿真。
随后调用 `approve_simulation_plan`，把同一网表审批绑定到经过安全校验的
`ExperimentSpec`（分析命令、测量和验收限制）；执行时将三份凭证传给
`run_verified_circuit_experiment`，它会在成图和仿真前再次验证绑定。

不要把 `DEEPSEEK_API_KEY` 放入 MCP 子进程配置。该凭据只属于 Harness。
MCP 服务器命令在 agent 沙箱之外执行，应当只安装可信发布物并固定版本。

## English summary

This package is an installable DeepSeek Harness bundle for Multisim MCP. Install
it with `dsh plugin --profile web add multisim-mcp-dsh-plugin@1.3.0`, set
`MULTISIM_MCP_PYTHON` to the 32-bit Python interpreter containing the compatible
`multisim-mcp` release, and restart the profile. Model credentials remain in
Harness and are never forwarded to the MCP child process. Maintainer release
instructions are in `docs/DEEPSEEK_HARNESS_NPM_RELEASE.md`.

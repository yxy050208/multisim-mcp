# Changelog / 更新日志

本项目遵循语义化版本的预发布形式。中文为主要说明，英文摘要紧随其后。

## [Unreleased]

### 中文

- 新增第一版传输无关 EDA Core：严格版本化 `CircuitDesign`、`DesignPatch` 和
  `ArtifactSet`，提供可逆有界补丁、模型来源和 SHA-256 产物清单。
- 新增 `EdaBackend` 能力发现协议、后端注册/调度服务和可注入的 Multisim 适配器；
  核心不依赖 MCP/COM，并以无 COM 假后端在 Python 3.10 与 32 位 Python 3.12 验证。
- 新增失败关闭的 `CircuitDesign` 与受限 SPICE 转换边界；
  `create_schematic_from_netlist` 已作为首个工具通过应用服务执行，公开签名和返回结果
  保持兼容，并通过真实 Multisim 14.3 与双 LM324 宏模型回归。
- `run_spice_netlist` 已接入同一应用服务，保留可选输出目录、超时、返回点数、覆盖和
  危险命令双重授权契约；产物清单会去重临时/发布副本，并通过真实 10 V 分压器工作点
  回归得到 5 V 输出。
- 新增传输无关 `ExperimentRequest` 与 `ExperimentApplicationService`，同步、验证和
  持久 worker 实验共用可注入事务入口；真实瞬态门禁生成 453 点数据、15 个完整文件
  和 15 个安全 Resource 句柄，同时保持 MCP 结果与 job 存储格式兼容。
- 根据五路波形课程设计真实回归，新增时域 `frequency` 与 `thd` 验收指标；支持测量
  窗口、边沿、阈值、迟滞、最少周期数、基波频率和谐波阶数，并将结果直接写入
  `verification.json` 与正式实验报告。
- 新增课程设计反馈记录，明确等效模型/厂商模型证据边界、联动频率冲突和自动原理图
  布局限制，避免把等效模型 PASS 误报为实物验收完成。
- 新增内联厂商 `.subckt` 宏模型递归展开：保留嵌套依赖、局部节点、模型引用和
  `PARAMS:` 覆盖，生成 Multisim 稳定参考编号，并以 `editable_model_coverage`
  区分完整、部分和仅载体证据；两级 LM324 真实事务回归通过。
- 新增 2.0 综合路线图，确定先平台化、再纠错优化、开放仿真后端、可视化工作台和
  KiCad 工程输出的开发顺序。
- 新增 DeepSeek 与官方 DeepSeek Harness 兼容说明，包括凭据边界、工具规模、
  Resources/Prompts 当前限制和版本验证矩阵。
- 配置生成器新增 `deepseek-harness` 客户端，输出官方 MCP Client 使用的 Cordis
  插件片段，并校验上游 `serverName` 约束。
- 新增四种服务端 Tool Profile；默认 `full` 保持完整工具兼容，其他档案可减少
  DeepSeek Harness 等客户端的工具 schema 上下文占用。
- 新增列出、分页读取、受控导出和汇总实验产物的四个 Tool 等价入口，供暂不消费
  MCP Resources 的客户端使用；导出限定在显式批准的根目录内。
- 新增五个版本化 DeepSeek Harness Skill，覆盖创建、纠错、比较、报告和指标验证；
  `harness-skills` 命令可安全安装到项目 `.dsh/skills`，默认拒绝覆盖。
- 新增机器可读 Harness 兼容清单、确定性本地门禁和每周非阻塞上游版本漂移监控。
- 新增可独立安装的 `multisim-mcp-dsh-plugin` bundle 源码，以及隔离、无 API Key、
  固定官方 dsh 版本的配置组合与真实启动烟雾测试。

### English

- Added the first transport-neutral EDA core with strict versioned
  `CircuitDesign`, reversible bounded `DesignPatch`, and hashed `ArtifactSet`
  objects.
- Added the `EdaBackend` capability protocol, backend dispatch service, and an
  injectable Multisim adapter with no-COM tests on Python 3.10 and win32 3.12.
- Added a fail-closed `CircuitDesign`/limited-SPICE conversion boundary and
  routed `create_schematic_from_netlist` through the application service while
  preserving its public contract, including a real dual-LM324 Multisim test.
- Routed `run_spice_netlist` through the same application service while
  preserving optional publication, timeout, point-limit, overwrite, and unsafe
  command gates; a real 10 V divider operating-point regression produced 5 V.
- Added transport-neutral `ExperimentRequest` and `ExperimentApplicationService`
  boundaries shared by synchronous, verified, and durable-worker experiments;
  a real transient gate produced 453 points and the complete 15-file transaction.
- Added time-domain `frequency` and `thd` verification metrics after a real
  five-output waveform-generator regression, including explicit measurement
  windows, edge/threshold/hysteresis controls, minimum cycles, fundamental
  frequency, and harmonic count.
- Documented the evidence boundary between equivalent and vendor models plus
  linked-range and automatic-schematic-layout limitations found by the course
  design workflow.
- Added recursive editable expansion for compatible inline vendor `.subckt`
  models, including nested dependencies, scoped nodes, instance parameters,
  stable Multisim references, and explicit editable-model coverage status.
- Added the post-1.0 platform, optimization, multi-EDA, and visual-workbench roadmap.
- Documented the DeepSeek and official DeepSeek Harness compatibility baseline.
- Added a `deepseek-harness` client target that renders a validated Cordis MCP
  plugin fragment without forwarding model credentials to the MCP process.
- Added four server-side tool profiles while preserving the complete `full`
  profile as the default.
- Added four Tool equivalents for listing, paginating, exporting, and summarizing
  experiment artifacts when a client does not consume MCP Resources.
- Added five versioned DeepSeek Harness skills plus a safe, no-clobber project
  installer for `.dsh/skills`.
- Added a machine-readable Harness compatibility manifest, deterministic local
  gate, and weekly non-blocking upstream drift monitor.
- Added an installable `multisim-mcp-dsh-plugin` source bundle and an isolated,
  credential-free smoke test against the pinned official dsh CLI.

## [1.0.0] - 2026-08-10

### 中文

- 迁移到 MCP Python SDK 2.x；同一 stdio 服务兼容 `2026-07-28` 和旧协议客户端。
- 将所有工具调用串行到专用 COM 线程，适配 SDK 2 的同步 handler 线程模型。
- 增加 11 个 `multisim://experiments/...` 实验资源模板、2 个扫描资源模板和重启后
  重新注册工具。
- 增加创建实验、调试、比较、报告和指标验证五个中英双语 Prompt。
- 为完整实验及资源注册结果增加明确的 output schema 和运行时结构校验。
- 增加 32 位 Windows 可安装的加密依赖边界及现代/旧协议、资源安全测试。
- 增加持久实验任务状态机，以及提交、查询、列出、取消和安全重试任务的 MCP 工具与状态 Resource。
- 将异步实验隔离到独立 worker 进程，支持排队、检查点、进度、取消、总超时、
  心跳超时、崩溃检测和 MCP 重启后的安全重排队。
- 为输出目录增加同名任务占用检查与跨进程文件租约；完整产物仍以事务方式发布。
- 增加版本化 `ExperimentSpec`、13 类确定性测量、逐项
  PASS/FAIL/未验证结论，以及理论值/仿真值/误差的结构化比较。
- 增加参数、容差、温度和可复现 Monte Carlo 扫描，包含 100 次硬上限、事务式
  汇总产物、MCP Resources 和持久 worker 支持。

- 增加 13 个不依赖 NI 数据库资产的可移植元件适配器，覆盖高价值模拟、功率、时序数字与单比特混合信号模型。
- 增加数据万用表、Bode Plotter 与 Logic Analyzer MCP 工具；缺少相位证据时明确返回不可用。
- SPICE3 ASCII raw 解析器新增复数 AC 数据、幅值、实部、虚部与相位支持。
- 完整实验自动输出中英双语独立 HTML/PDF 与带 SHA-256 的 `manifest.json`，并新增 5 个 Resource 模板。
- 公开严格声明式 JSON 元件适配器接口、贡献示例和兼容性矩阵；禁止执行代码和外部文件指令。
- 本地模板生成器改用当前 Multisim 自动创建的空白电路作为工程骨架，写入 schema 2
  manifest；`doctor` 拒绝可能静默丢失元件的旧 schema 1 包。
- 完成 116 项无 COM 测试、32/64 位 Python 安装回归、现代/旧 MCP 协议握手、
  代码型 wheel/sdist 审计以及真实 Multisim 14.3 元件与代表性仿真回归。

### English

- Migrated to MCP Python SDK 2.x with one stdio server serving both the
  `2026-07-28` and legacy protocol eras.
- Serialized every tool call onto a dedicated COM-initialized worker thread.
- Added sixteen experiment resource templates, two sweep resource templates,
  re-registration, five bilingual prompts, and validated structured results
  for the high-level workflow.
- Added a 32-bit Windows-compatible cryptography constraint and dual-era,
  resource-security, and structured-output tests.
- Added a durable experiment-job state machine with queueing, progress,
  cancellation, total/heartbeat timeouts, restart recovery, and a status
  resource.
- Isolated asynchronous experiments in restartable subprocesses and added
  cross-process output leases plus structured crash/hang diagnostics.
- Added versioned design requirements, deterministic measurements, strict
  pass/fail/unverified verdicts, and theory-versus-simulation errors.
- Added parameter, tolerance, temperature, and seeded Monte Carlo sweeps with
  a 100-run cap, transactional summaries, resources, and durable jobs.
- Added thirteen portable component adapters for high-value analog, power,
  sequential-digital, and one-bit mixed-signal models without NI database assets.
- Added data-backed multimeter, Bode Plotter, and Logic Analyzer tools.
- Added complex SPICE3 raw parsing with magnitude, real, imaginary, and phase data.
- Added standalone Chinese/English HTML and PDF reports, a SHA-256
  reproducibility manifest, five resources, and a strict declarative adapter API.
- Rebuilt user-local pack scaffolding from a blank circuit created by the
  installed Multisim version, added a schema-2 manifest, and made `doctor`
  reject legacy schema-1 packs that can silently omit components.
- Completed 116 COM-free tests, 32/64-bit Python installation checks, modern
  and legacy MCP handshakes, code-only artifact audits, and real Multisim 14.3
  component and representative-simulation regressions.

## [0.1.0-alpha.3] - 2026-08-09

### 中文

- 新增保持无参数 stdio 启动兼容的 `multisim-mcp doctor`、`serve` 和
  `config` 命令。
- `doctor` 默认无副作用检查 Python 位数、pywin32、Multisim COM 注册、模板包和
  `.ms14` 编解码器，并提供稳定 JSON、可选严格退出码和显式 `--connect` 激活验证。
- 配置生成器可输出 Claude Desktop JSON、Codex TOML 和通用 stdio JSON，默认
  仅预览且拒绝静默覆盖文件。
- 将 pywin32 首次导入的 COM 缓存输出隔离到 stderr，避免污染 MCP/JSON stdout。
- 当 pywin32 的 `gen_py` 生成包装缓存损坏时，自动回退到动态 COM Dispatch，
  无需删除用户缓存即可连接 Multisim。

### English

- Added backward-compatible `doctor`, `serve`, and `config` CLI commands.
- Added default-side-effect-free checks for Python architecture, pywin32, COM
  registration, the local template pack, and `.ms14` codecs, with stable JSON,
  an optional strict exit code, and an explicit `--connect` activation probe.
- Added Claude Desktop JSON, Codex TOML, and generic stdio configuration
  fragments with preview-first overwrite protection.
- Redirected pywin32 import chatter away from MCP and JSON stdout.
- Added a dynamic COM Dispatch fallback for stale or corrupt pywin32 `gen_py`
  wrapper caches without deleting user data.

## [0.1.0-alpha.2] - 2026-08-09

### 中文

- 增加 Linux/Docker `introspection-only` 模式，可完成 MCP 初始化和工具发现。
- 将 `pywin32` 限定为 Windows 依赖；非 Windows 自动化调用返回明确兼容性提示。
- 增加最小权限 Glama 验证镜像、严格 Docker 构建上下文和 Ubuntu 容器握手 CI。
- 实际 Multisim 电路生成、仿真和导出仍仅支持本地 Windows + 32 位 Python。

### English

- Added a Linux/Docker `introspection-only` mode for MCP initialization and
  tool discovery.
- Made `pywin32` Windows-specific and added explicit diagnostics for unsupported
  automation runtimes.
- Added a least-privilege Glama validation image, a strict Docker build context,
  and an Ubuntu container handshake check.
- Real Multisim generation, simulation, and export remain Windows-only.

## [0.1.0-alpha] - 2026-08-09

### 中文

- 建立从受限 SPICE 网表到可编辑 Multisim 原理图、真实仿真、CSV/SVG 和
  Markdown 报告的完整工作流。
- 支持主要模拟 SPICE 原语、2–16 端通用子电路、组合逻辑和 JK 触发器。
- 接入原生 XFG 函数发生器和 XSC 示波器状态。
- 增加命令白名单、外部文件指令拦截、覆盖保护和本机 stdio 安全边界。
- 增加本地模板包生成器，避免在公开仓库分发许可不明确的 NI 派生 XML。
- 发布 `multisim-mcp==0.1.0a1` 到 PyPI，并登记到官方 MCP Registry。
- 48 项无 COM 测试、19 项安装包资源测试和 8 组真实 Multisim 元件族回归通过。

### English

- Added the complete constrained-netlist → editable schematic → real simulation
  → CSV/SVG/Markdown workflow.
- Added major analog SPICE primitives, generic 2-16-pin subcircuits, logic gates,
  JK timing, and native XFG/XSC instrument state.
- Added safe command validation, overwrite protection, local-pack generation,
  and release-time asset separation.
- Published `multisim-mcp==0.1.0a1` to PyPI and the official MCP Registry.
- Verified 48 COM-free tests, 19 installed-package resource tests, and eight
  real Multisim component-family groups.

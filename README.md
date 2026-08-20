# Multisim MCP + Skills

[![Glama MCP server score](https://glama.ai/mcp/servers/yxy050208/multisim-mcp/badges/score.svg)](https://glama.ai/mcp/servers/yxy050208/multisim-mcp)

[中文（当前）](README.md) | [English](README.en.md)

让 AI Agent 根据实验要求自动生成 Multisim 电路、运行仿真、提取实验数据，并导出
电路图、CSV、波形图和实验报告。

> 当前稳定发行版为 `v1.0.0`。项目非 NI 官方产品，需要本机安装并授权
> Multisim 14+；当前 COM 运行时使用 32 位 Python。

四个开发阶段和 1.0 发布门禁见 [`1.0 路线图`](docs/ROADMAP_TO_1.0.md)。

[PyPI 安装包](https://pypi.org/project/multisim-mcp/) ·
[官方 MCP Registry 条目](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[GitHub Release](https://github.com/yxy050208/multisim-mcp/releases/tag/v1.0.0)

## 开源发布状态

`1.0.0` 已发布到 PyPI，并以 `io.github.yxy050208/multisim-mcp` 收录到官方
MCP Registry。由本地 NI 样例提取的 XML 模板不属于
MIT 代码授权范围，公开仓库默认不应包含这些文件。用户需要运行
`tools/bootstrap_local_component_pack.py`，从自己已授权的 Multisim 安装生成本地模板包。

请不要上传 `analysis/`、`.ms14`、解码 XML、类型库转储、实验输出或当前包含 142 个
本地模板的开发 wheel。完整发布步骤见 [`docs/PUBLISHING.md`](docs/PUBLISHING.md)。

## 已实现的完整闭环

`run_circuit_experiment` 可以从同一份受限 SPICE 网表完成：

1. 网表和实验命令安全校验。
2. 生成可编辑 `.ms14` 原理图。
3. 由真实 Multisim 打开并反向枚举验证。
4. 导出原理图 PNG。
5. 运行 DC、AC、瞬态或工作点实验。
6. 导出 raw、CSV、SVG 波形和命令日志。
7. 生成 Markdown、中英双语独立 HTML/PDF 报告及带 SHA-256 的 `manifest.json`。

对于较长实验，`submit_circuit_experiment` 持久任务接口会立即返回
`job_id`，可通过 `get_experiment_job`、`list_experiment_jobs`、
`cancel_experiment_job`、`retry_experiment_job` 或 `multisim://jobs/{job_id}` 查询、
取消和重试任务。每个实验
运行在隔离 worker 进程中；worker 崩溃或心跳超时不会拖垮 MCP 服务，服务重启后未完成
任务会安全地重新排队。

1.0 还加入了可计算的设计验收与批量实验：

- `run_verified_circuit_experiment` 接收版本化 `ExperimentSpec`，自动测量时域频率、
  THD、增益、带宽、截止频率、上升时间、过冲、纹波、功耗等指标，并把逐项
  `pass` / `fail` / `unverified` 结论写入 `verification.json` 和实验报告。
- `measure_experiment` 与 `verify_experiment_requirements` 可对已注册实验重新计算
  指标；信号或证据缺失时只返回 `unverified`，不会猜测结果。
- `plan_experiment_sweep`、`run_experiment_sweep`、`submit_experiment_sweep` 支持
  参数、容差、温度与可复现 Monte Carlo 扫描。每次扫描最多 100 个运行点，长扫描
  复用持久任务的取消、超时、崩溃恢复与输出锁。
- 扫描输出 `summary.json`、扁平 `data.csv` 和每个运行点的原始产物，并通过
  `multisim://sweeps/{sweep_id}/summary|data` 读取。

验收请求的核心结构如下；`operator` 支持 `at_least`、`at_most`、`between` 和
`approximately`：

```json
{
  "spec": {
    "schema_version": 1,
    "title": "分压器验收",
    "netlist": "VIN vin 0 DC 10\nR1 vin vout 1k\nR2 vout 0 1k\n.end\n",
    "commands": "dc VIN 0 10 1",
    "requirements": [
      {
        "id": "gain",
        "metric": "gain",
        "signal": "V(vout)",
        "reference_signal": "V(vin)",
        "operator": "approximately",
        "target": 0.5,
        "tolerance_percent": 1
      }
    ],
    "theoretical_values": {"gain": 0.5}
  },
  "output_dir": "C:\\experiments\\divider-verified"
}
```

扫描的 `mode` 可设为 `parameter`、`tolerance`、`temperature` 或
`monte_carlo`。元件数值使用有限数字替换已声明的 `{{NAME}}` 占位符；建议先调用
`plan_experiment_sweep` 检查完整展开结果，再提交真实运行。

已经在 Multisim 14.3 上完成分压器、耦合电感、数字门/JK 时序以及
函数发生器 + 示波器联合实验的真实验证。

1.0 加入不分发 NI 数据库资产的可移植元件与数据仪器：

- `@TRANSFORMER`、`@POTENTIOMETER`、`@RELAY`、`@CRYSTAL`、功率二极管/MOS；
- `@DFF`、`@TFF`、`@COUNTER4`、`@SHIFT_REGISTER4`、`@ADC1`、`@DAC1`；
- `read_virtual_multimeter`、`analyze_bode_response` 和 `analyze_logic_signals`；
- `export_formal_experiment_report`，以及 5 个新的正式报告/清单 Resource。

适配器语法与社区 JSON 接口见 [`docs/COMPONENT_ADAPTERS.md`](docs/COMPONENT_ADAPTERS.md)，
真实版本边界见 [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md)。
1.0 真实回归覆盖适配器原理图打开/回导、变压器瞬态、继电器与功率器件工作点、
晶振 AC、DFF 瞬态以及双语正式报告的完整事务发布。

## 能力成熟度

稳定：

- Multisim COM 连接、打开、保存、枚举和导出。
- DC/AC/瞬态分析、波形输入注入、RLC 读写。
- 安全子集 SPICE 实验和 raw/CSV 解析。
- MCP stdio、运行环境诊断和报告生成。
- 持久实验队列、进度/取消/超时、输出锁和崩溃/无响应 worker 恢复。
- 版本化实验指标、严格 PASS/FAIL/未验证判定，以及四类确定性批量扫描。

实验性：

- 自动原理图支持 R/L/C、标量及波形电压/电流源、B/E/F/G/H 受控源、
  K/T/O/U 耦合与传输线、二极管、NPN/PNP、NMOS/PMOS、JFET/MESFET、
  电压/电流控制开关、五端运放及 2–16 端通用 X 子电路；扩展族暂用通用载体符号。生成后会通过
  Multisim 反向网表确认器件没有被静默丢弃。
- 直接粘贴的厂商 `.subckt` 宏模型可递归展开为可编辑原生器件，保留嵌套依赖、
  局部节点和 `PARAMS:` 参数；`editable_model_coverage` 会区分完整展开、部分展开和
  仅载体状态，详见 [`docs/VENDOR_SPICE_MODELS.md`](docs/VENDOR_SPICE_MODELS.md)。
- 已加入原生 NOT/AND/OR/NAND/NOR/XOR/XNOR 和 JK 触发器预览；
  原理图打开/回导、组合逻辑真值表和 JK 翻转时序均已真实验证。
- 支持原生 XFG 函数发生器和四通道 XSC 示波器状态，实验波形同时导出为
  CSV、SVG 和 Markdown 报告。
- 自动生成的原理图探针暂不作为实验数据来源；实验数据来自同一网表经 Multisim
  命令引擎执行的结果。

## 快速开始

从 PyPI 安装到 32 位 Python 环境：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==1.0.0"
C:\path\to\python32\Scripts\multisim-mcp.exe
```

Linux/Docker 仅提供 MCP 工具发现和兼容性诊断，不能运行 Multisim 仿真。容器中的
`runtime_status` 会返回 `introspection-only`；所有 COM 自动化能力仍要求上述 Windows
环境。根目录 `Dockerfile` 用于 Glama 等目录验证 MCP 协议和工具定义。

从源码安装并生成本地元件模板包：

```powershell
cd mcp_server
.\setup.ps1 -Python C:\path\to\python32\python.exe
npm install --global electronics-workbench-decoder@0.2.0
cd ..
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
C:\path\to\python32\python.exe .\tools\bootstrap_local_component_pack.py `
  --output C:\MultisimMcp\component-pack
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\component-pack'
cd mcp_server
.\run_server.ps1
```

模板包生成器会连接已授权的 Multisim，并新建一个临时空白电路以取得与当前安装版本
一致的工程骨架；执行前请保存正在编辑的工作。alpha 版本生成的 schema 1 包需要重建。

`v1.0.0` 提供安装诊断和配置生成命令。默认情况下它们不会启动
Multisim，也不会修改现有客户端配置：

```powershell
# 人类可读诊断；完整工作流未就绪时给出逐项修复建议
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang zh

# 可选：显式启动/连接 Multisim，验证许可证和 COM 激活
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang zh --connect

# 便于 Agent/脚本解析的稳定 JSON；--strict 可用于 CI
C:\path\to\python32\Scripts\multisim-mcp.exe --json doctor

# 输出 Claude Desktop JSON 片段
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client claude-desktop `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack

# 输出 Codex config.toml 片段
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client codex `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack

# 输出 DeepSeek Harness Cordis 插件片段
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client deepseek-harness `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack `
  --work-dir C:\msre_exp `
  --artifact-export-dir C:\MultisimMcp\exports `
  --tool-profile experiment

# 在 Harness 项目根安装五个双语实验 Skill
C:\path\to\python32\Scripts\multisim-mcp.exe harness-skills --output .dsh/skills
```

配置生成器默认只打印可复制片段；`--output` 写入新文件，除非再传入 `--force`，
否则不会覆盖已有文件。它不会自动合并 Claude Desktop、Codex 或 Harness 的现有配置。
DeepSeek 模型与官方 Harness 的分层、凭据边界和版本兼容性见
[`DeepSeek / Harness 适配说明`](docs/DEEPSEEK_HARNESS.md)。
`--tool-profile core|experiment|optimization|full` 可限制客户端发现的工具；
省略时保持 55 个工具全部可用的 `full` 兼容模式。产物导出只有在设置
`--artifact-export-dir` 后可用，并且只能写入该目录之下。
Harness Skill 安装默认不覆盖现有文件；需要恢复打包版本时显式增加 `--force`。
仓库维护者可用 `python tools/check_deepseek_harness_compat.py --json` 验证固定的
Harness 本地契约；版本与上游检查细节见适配说明。
需要把集成作为 Harness 插件安装时，可使用
[`integrations/deepseek-harness`](integrations/deepseek-harness) 中的独立 bundle；
它目前支持本地源码安装，尚未发布到 npm。维护者的首次 2FA 发布与后续 OIDC
暂存流程见 [`npm 发布手册`](docs/DEEPSEEK_HARNESS_NPM_RELEASE.md)。

手工 MCP 客户端配置：

```json
{
  "mcpServers": {
    "multisim": {
      "command": "C:\\path\\to\\python32\\python.exe",
      "args": ["-m", "multisim_mcp.server"]
    }
  }
}
```

详细安装、工具、安全开关和测试说明见 [`mcp_server/README.md`](mcp_server/README.md)。
元件覆盖和剩余边界见 [`docs/COMPONENT_COVERAGE.md`](docs/COMPONENT_COVERAGE.md)。
从 alpha 升级请阅读 [`docs/MIGRATION_TO_1.0.md`](docs/MIGRATION_TO_1.0.md)；任务与
实验恢复流程见 [`docs/RECOVERY.md`](docs/RECOVERY.md)。
1.0 之后的纠错、优化、多 EDA 后端和可视化工作台计划见
[`2.0 综合路线图`](docs/ROADMAP_TO_2.0.md)。阶段 A 已加入第一版传输无关
[`EDA 核心与后端边界`](docs/EDA_CORE.md)和受限 SPICE 转换器；首个原理图工具已通过
应用服务执行，现有 MCP 工具签名保持兼容。

## 仓库结构

- `mcp_server/`：MCP server、COM adapter、原理图构建器和测试。
- `skills/multisim-workflow/`：面向 Agent 的实验工作流。
- `docs/`：架构、实测流程和能力边界。
- `analysis/`、`tools/`：互操作研究材料；发布前需要独立做来源和隐私审计。

## 安全

- 默认只允许 `op`、`dc`、`ac`、`tran` 实验命令。
- 任意 Multisim 命令文件默认关闭，需要显式不安全环境开关。
- MCP 启动过程不会自动安装 Python 或 npm 依赖。
- 默认拒绝覆盖已有实验文件。
- 仅适合可信本机 stdio 使用，不应直接暴露到网络。

详见 [`SECURITY.md`](SECURITY.md)。

## License

项目自有代码采用 MIT License。NI Multisim、商标、格式以及本地安装样本仍受其各自
权利和许可条款约束。本仓库不应提交 NI 二进制、许可证材料或未确认可再分发的样本。

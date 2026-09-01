# 五波形课程设计 Demo / Five-waveform course-design demo

这份 Demo 把课程设计要求收敛成一个可复现的验收契约，作为比赛演示的第一条
稳定路径：五路输出、+10 V 单电源、600 Ω 负载、测试端子，以及频率/峰峰值/正弦
THD 的显式判据。它复用 MCP 的 `ExperimentSpec`、安全分析命令、`verification.json`、
CSV、波形图和双语报告链路。

## 重要的证据边界

仓库内置的是行为级参考网表：它使用明确的 `PULSE`、`PWL`、`SIN` 电压源复现五路
目标波形，用于验证产品的“规格 → 实验 → 证据 → 报告”闭环。它不等同于课程要求的
原生 555、74LS74、LM324 元件级电路，也不能替代 Multisim 厂商宏模型、元件容差、
面包板寄生和示波器实测。

将真实 `.ms14`/原生网表接入时，应把 manifest 的 `netlist_kind` 改为
`native-multisim`。但这个标签本身不会触发“元件级通过”：HE555、74LS74、LM324、
1N4007 四类关键模型都必须记录准确型号、实现方式、来源、许可证说明、Multisim 后端
以及产物 SHA-256；同一实现还必须在真实 Multisim 中对准确的 12 项课程判据取得
12/12 PASS。任意一项缺失时 `component_level_claim` 都保持 `false`。

## 规格摘要

| 通道 | 目标频率 | 目标幅度 | 预期实现路径 |
| --- | ---: | ---: | --- |
| 方波 I / Square I | 20–50 kHz | 1 Vpp | 555 astable |
| 方波 II / Square II | 5–10 kHz | 1 Vpp | 74LS74 divider |
| 三角波 / Triangle | 5–10 kHz | 3 Vpp | 74LS74 + LM324 integrator |
| 正弦波 I / Sine I | 20–30 kHz | 3 Vpp | LM324 shaping filter |
| 正弦波 II / Sine II | 250 kHz | 8 Vpp | LM324 fixed-frequency stage |

每路都生成频率和峰峰值判据；两路正弦波额外生成 `THD <= 5%` 判据，因此默认规格
包含 12 项可验证要求。频率范围、幅度和 THD 的容差应以课程书面要求和真实测量
能力为准，不能由 Agent 静默放宽。

## 生成 Demo 包

在仓库的 `mcp_server` 目录执行：

```powershell
python -m multisim_mcp.cli course-demo `
  --output ..\examples\course_waveform_demo `
  --json
```

输出目录包含：

- `course-demo-manifest.json`：中英通道、供应、负载、端子和证据边界；
- `course-demo-spec.json`：可直接交给 `run_verified_circuit_experiment` 的 12 项规格；
- `behavioral-reference.cir`：跨后端的行为级参考网表；
- `analysis-commands.txt`：默认 `tran 50n 400u`。
- `course-bom.csv`：从用户提供图片转录的 35 行、合计 55 件 BOM；采购前需人工核对；
- `course-component-evidence.template.json`：四类关键模型的证据填写模板；
- `course-experiment-evidence.json`：运行后从完整性受检实验中提取的后端和 12 项摘要；
- `component-readiness.json`：机器可读的元器件级门禁结果和阻塞原因；
- `native-implementation-plan.md`：原生实现步骤及固定四分频的联动范围冲突。

只把网表标成原生不会产生虚假声明：

```powershell
python -m multisim_mcp.cli course-demo `
  --output ..\examples\course_waveform_native `
  --netlist .\native-course.cir `
  --netlist-kind native-multisim `
  --component-evidence .\component-evidence.json `
  --run `
  --json
```

`--run` 成功后，CLI 会校验 `manifest.json` 中记录的 `verification.json` SHA-256，提取
后端和准确的 12 个 requirement ID，并重新计算元器件级门禁。也可以在不重跑时通过
`--experiment-evidence` 导入已经导出的证据对象；它不能与 `--run` 同时使用。

## 运行参考实验

拥有 ngspice 时，可在 PowerShell 中显式选择开放后端：

```powershell
$env:MULTISIM_MCP_EXPERIMENT_BACKEND = "ngspice"
python -m multisim_mcp.cli course-demo `
  --output ..\examples\course_waveform_demo `
  --run `
  --json
```

拥有本机授权 Multisim 时，删除该环境变量或设置为 `multisim`，并先运行
`python -m multisim_mcp.cli doctor --connect --strict`。运行成功后，`experiment` 子目录
会保留原理图/便携图、`data.csv`、`result.raw`、`plot.svg`、`verification.json`、
Markdown 以及双语 HTML/PDF 报告。若后端未安装或仿真失败，系统必须报告
`unverified`/失败原因，不能伪造 PASS。

## MCP 调用方式

Agent 可以先调用 `build_course_waveform_demo` 获取 manifest、完整 BOM、模型策略和 spec，
再把返回的 `spec` 原样交给 `run_verified_circuit_experiment`。使用真实原生网表时，可把
`component_evidence` 和 `experiment_evidence` 一并传给前一个工具；服务器将按与 CLI 相同
的严格门禁计算声明，不接受仅由调用方设置布尔值。

## 下一阶段

1. 使用本机 NI 元件库或用户有权使用的模型填完四类关键器件证据，不把许可受限模型
   复制进开源仓库。
2. 依据 35 行 BOM 完成一张模块化、可读的原生 Multisim 原理图，并人工核对图片转录。
3. 用真实 Multisim 完成五路合并瞬态实验，确认分频联动范围、负载压降和 THD。
4. 把通过的 `.ms14`、网表散列、模型许可证说明和报告作为比赛证据包；行为级参考仅
   作为跨平台 CI 回归。

本机数据库路径、隔离要求和当前两项已验证结果见
[`原生元器件探测`](NATIVE_COMPONENT_PROBING.md)。

## English summary

The demo is a reproducible five-channel verification contract for the course brief. It covers
10 frequency/peak-to-peak checks plus two sine-wave THD checks, and reuses the MCP's safe
experiment, artifact, and bilingual-report pipeline. The bundled netlist is explicitly a
behavioral reference, not a claim of native 555/74LS74/LM324 implementation. A native Multisim
netlist must be run on the licensed local backend and retain model provenance, hashes, and
verification evidence before it is presented as a component-level pass. The machine gate now
requires all four named model identities plus an integrity-checked Multisim 12/12 result; merely
setting `netlist_kind=native-multisim` can never create the claim.

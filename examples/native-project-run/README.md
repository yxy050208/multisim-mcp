# 原生工程副本实验

这个入口将已有 `.ms14` 工程复制到新的输出目录，在副本上修改 R/L/C 参数，
通过 Multisim COM 直接执行直流工作点分析，然后导出工程、原理图、连接表、
BOM、CSV、原始 COM 数据和 HTML/Markdown 实验记录。

本机已实测 Multisim 14.3 + 32 位 Python。其他版本尚未实测；版本号识别
不代表元件映射、API 输出通道或分析能力已通过验证。

## 运行

在仓库根目录，使用已安装项目依赖的 Python：

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
python -m multisim_mcp.cli native-project-run `
  --request examples/native-project-run/request.json `
  --source 'C:\MyCircuits\RLC Values.ms14' `
  --output 'C:\MyExperiments\rlc-baseline' --json
```

默认只预览，不连接 COM、不创建输出目录。加 `--execute` 执行：

```powershell
python -m multisim_mcp.cli native-project-run `
  --request examples/native-project-run/request.json `
  --source 'C:\MyCircuits\RLC Values.ms14' `
  --output 'C:\MyExperiments\rlc-20k' --set R1=20k --execute --json
```

源文件需来自本机授权的 NI API Toolkit `RLC Values.ms14` 样例，未随仓库分发。
参数可重复指定，例如 `--set R1=20k --set R2=50k`。每次输出目录必须是新目录。
执行成功退出码为 0，执行失败为 1，输入/预检错误为 2。

## 当前支持范围

- 单工程、R/L/C 数值变更，以及原生 COM 的 `op`、`ac`、`tran` 分析。
- 请求中的 `outputs` 必须与该工程的 `enum_outputs` 结果完全一致。
  样例使用 `V(OutProbe)` 和 `I(OutProbe)`；普通网络名或生成器的普通探针
  不一定是 COM API 输出通道。缺少输出时写入失败记录和可用通道列表。
- 修改前读取全部原值，每次写入后回读校验，成功和异常路径均恢复会话中的原值。
  输出工程保存的是实验参数，原始源文件另以 SHA-256 检查保持不变。
- 缺少输出、COM 分析失败或超时均终止，不改用原始 SPICE 文本。
  `ReportNetlist` 返回连接表，不是可以交给 `source` 执行的 SPICE 网表。
- 自然语言模型调用、自动增加 API 探针和多板优化尚未接入本入口。
  AC 使用 `ac lin|dec|oct N start stop`，瞬态使用 `tran step stop`；本入口不会静默降级实验类型。

## 完整数据回归（2026-09-08）

使用 `all-analyses.json` 可在同一个入口连续执行 OP、三种 AC 扫频和双通道瞬态。
将上述命令的 `--request` 改为 `examples/native-project-run/all-analyses.json` 即可复现。
本机 Multisim 14.3 已实际完成全部 5 项分析：OP 1 点，linear 11 点，decade 11 点，
octave 34 点，双通道瞬态各 208 点、时间范围 0–2 ms。

- 扫频枚举从本机 `MSInterface.dll` 核实：decade=0、octave=1、linear=2。
- `ReportNetlist` 必须先于 `EnumOutputs`。反向调用顺序在本机可导致瞬态电流通道无法就绪。
- CSV 保留所有返回样本和独立变量。AC 导出频率、实部、虚部、幅值、相位（度）；
  测量摘要统计幅值，不将实部误称为幅值。
- 瞬态所有输出在同一次仿真中注册和读取；时间轴不同、子通道超时、采样被截断或未覆盖
  请求时长时失败，不插值补齐。`step` 用于请求采集速率，不是 SPICE 求解器最大积分步长；
  导出时间轴为 COM 原始求解器时间点，可能不是等间隔。
- 原始数据保留上限为 100000 点，超过上限失败；不会用抽样数据声称完整波形。
  `max_points` 不再控制 CSV 的抽样。暂不支持额外瞬态起始时间、最大步长或 `uic` 选项。
- 瞬态样本均值不是时间加权均值；报告明确标识这一点。
- 每次分析保存独立 `.ms14` 快照和哈希；部分实验失败时不会把整组标记为完成。

本节实例使用已有授权 API Toolkit 工程，仅证明原生执行链路。
生成器的分压及 RC 闭环另由 [自动生成电路验收](../../docs/GENERATED_ANALOG_ACCEPTANCE.md) 复现和验证。

`run.json` 的 `completed` 仅表示执行成功；`verification_status` 仍为 `unverified`。
实验结果不自动证明电气正确、稳定、满足需求或版图合格。`manifest.json` 给出
全部证据文件的 SHA-256，COM 原始矩阵位于 `analysis-001/native-result.json`。

## 2026-09-07 本机回归

使用同一授权样例，R1=10 kΩ 时探针电流约 −251.558 µA，R1=20 kΩ 时约
−215.634 µA。两次原生 COM 分析均完成，源文件未变化，修改值恢复无错误；
导出原理图显示 R1=20 kΩ。输出电压接近 −15.09 V，属于接近负电源轨的工作点，
此例只验证工程参数确实进入原生分析，不作为电路优化或设计合理性验收。

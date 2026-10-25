# 真实 LM324AJ 双板联调合同（开源版）

本阶段把真实运放的单板准入扩展到一个可审计的双板闭环。范围严格限定为
Multisim 14.3、本机已授权的 `LM324AJ` A 单元（关联模型 `LM158_4`）和固定的
两级传感器前端。它提供开源版的基本多板联调能力，不是任意供应商器件的自动分板器。

## 固定拓扑

`sensor` 板包含 `V1/VCC/VSS/R1/C1/U1/RF1/RG1`，`conditioner` 板包含
`R2/C2/U2/RF2/RG2/RL`。两个运放均必须是 `LM324AJ`，且使用语义端子顺序：
`1IN+、1IN-、VS+、VS-、1OUT`。两板通过一个真实 `HDR1X4`：

| 引脚 | 网络 | 含义 |
| ---: | --- | --- |
| P1 | `stage1` | 前级模拟输出到后级输入 |
| P2 | `vcc` | 正电源 |
| P3 | `vss` | 负电源 |
| P4 | `0` | 地 |

分区、接口引脚、模型身份、供电极性和元件数量均由批准合同重新推导。`LM324M`、
其它版本或邻近引脚顺序不会被静默接受。

## 使用入口

```python
from multisim_mcp.spice_adapter import circuit_design_from_spice
from multisim_mcp.vendor_multiboard import (
    prepare_lm324aj_multiboard_contract,
    run_lm324aj_multiboard_acceptance,
)

design = circuit_design_from_spice(reviewed_lm324_netlist)
contract = prepare_lm324aj_multiboard_contract(design)
preview = run_lm324aj_multiboard_acceptance(contract, "new-output", execute=False)
result = run_lm324aj_multiboard_acceptance(
    contract, "new-output", execute=True, analysis="ac",
    analysis_options={"sweep_type": 0, "num_points": 10,
                      "start_frequency": 10, "stop_frequency": 10000},
)
```

TRAN 需要明确允许独立板运行的自适应采样轴做线性重采样：

```python
result = run_lm324aj_multiboard_acceptance(
    contract, "new-output", execute=True, analysis="tran",
    analysis_options={"sample_rate": 100000, "num_samples": 500,
                      "duration": 0.005, "series_alignment": "linear"},
)
```

预览不创建文件、不启动 COM。执行阶段复用通用原生验收器，逐板和完整参考工程均执行
保存、重开、ReportNetlist、原生 OP/AC/TRAN，并在分析前检查：

1. LM324AJ A 单元全部五个端子的网络；
2. `VCC` 正、`VSS` 负及所有 R/C 的 SI 值；
3. `LM324AJ/LM158_4` 身份、关联 `CiModel` 与模型正文指纹；
4. 两块板和完整参考的显式观测、原生采样轴与接口比较。

接收板的 AC/TRAN 实验使用合同内明确声明的上游 LM324AJ 电路片段；发送板实验同时
加入完整的接收端输入/负载夹具（R2/C2、第二级 LM324AJ 及其反馈/负载）。这些夹具只
复现逐板实验的边界条件，不加入物理板导出，也不被解释为线缆、PCB 或跨板联合仿真。
真实供应商结果只按 `native-vendor-sampled` 记录，不使用理想线性方程作为独立正确性
证明。LM324AJ 宏模型的跨工程数值比较门槛显式记录为 `1e-7 V`；它不是对任意模型的
通用放宽。

## 2026-10-13 原生结果

使用本机授权模板包 `component-pack-signal-20261011`、Multisim 14.3 和 Python 3.13
64 位主进程，最终合同的 OP、AC、TRAN 均为 `accepted/native-verified`。每种分析都
执行 `sensor`、`conditioner` 和完整参考三个工程，保存重开后核对 HDR1X4 P1–P4、
两块板的 LM324AJ A 单元五个端子、所有 R/C SI 值、`LM324AJ/LM158_4` 模型与
`CiModel`/正文指纹，并逐点比较接口和完整参考。

| 分析 | 设置 | 结果 |
| --- | --- | --- |
| OP | 原生工作点 | `accepted`，`stage1` 约 `0.1169719413 V`，`out` 约 `0.2667307758 V` |
| AC | 10 Hz–10 kHz、每十倍频程 10 点 | `accepted`，最大接口差约 `1.20e-15 V`，最大参考差约 `4.50e-11 V` |
| TRAN | 5 ms、100 kHz、线性重采样 | `accepted`，最大接口差约 `1.23e-10 V`，最大参考差约 `1.69e-10 V` |

原生工程和机器可读报告保存在本机忽略目录
`release/vendor-multiboard-lm324-20261013-{op,ac,tran}-native-v3/`；每个目录还包含
`physical/sensor/` 与 `physical/conditioner/` 两份不含实验夹具的保存重开板图、PNG、
SPICE、组件清单和引脚/模型读回。每个目录的 `sha256-manifest.json` 覆盖全部产物。
合同摘要在 `release/vendor-multiboard-lm324-20261013-contract-v3.json`。这些本地目录
包含授权模型和 Multisim 产物，不进入公开仓库。

同一固定合同随后用第二个需求变体 `100 mV / 增益 10 / 1 kHz / ±15 V / 100 kΩ` 重建，
并在 `release/vendor-multiboard-lm324-20261013-variant2-{op,ac,tran}-v1/` 中完成同样的
OP/AC/TRAN、物理板图和哈希门禁，三种分析均为 `accepted/native-verified`。这说明负载、
供电、增益和滤波参数会进入实际合同与夹具派生；它仍是两个已审核参数点，不代表任意
自然语言需求都已覆盖。

最终证据另外复制到本机忽略目录
`release/vendor-two-board-evidence-20261013-final/{gain5,gain10}-{dc,ac,tran}/`。
每个目录都包含 `vendor-contract.json`、`vendor-acceptance.json`、完整参考与逐板原始矩阵、
不含夹具的物理板图、HTML/Markdown 报告和 `sha256-manifest.json`。离线复核入口为：

```powershell
$env:PYTHONPATH = 'mcp_server'
python tools/verify_vendor_multiboard_evidence.py `
  release/vendor-two-board-evidence-20261013-final/gain5-dc `
  release/vendor-two-board-evidence-20261013-final/gain5-ac `
  release/vendor-two-board-evidence-20261013-final/gain5-tran `
  release/vendor-two-board-evidence-20261013-final/gain10-dc `
  release/vendor-two-board-evidence-20261013-final/gain10-ac `
  release/vendor-two-board-evidence-20261013-final/gain10-tran
```

该复核器不启动 COM，也不重新声称当前机器已仿真；它只证明归档合同、原始矩阵、
引脚/RLC/模型读回、接口比较、报告和文件哈希彼此一致。故意修改或重新计算错误哈希
不会让归档重新变成通过。

## 离线门禁与故障回归

`mcp_server/tests/test_vendor_multiboard.py` 覆盖固定分区、四针映射、14.3 版本关闭、
模型和端子顺序关闭、无 COM 预览，以及保存重开后的引脚、R/C、模型故障注入。通用
`circuit_fragment` 支持 `explicit-native-vendor-fixture` 和不含源的
`explicit-native-vendor-load-fixture`；后者只允许已审核 LM324AJ 与 R/C，用于复现接收
端输入负载。理想片段仍由独立节点解门禁。
`mcp_server/tests/test_vendor_multiboard_evidence.py` 覆盖无 COM 归档复核及 13 类故障
注入：哈希/漏项、RLC、夹具、探针、采样轴/虚部、重复板、门槛、物理板组件、引脚、模型、
报告和 CSV 改动。

## 边界

当前证据只适用于上述版本、模板包和拓扑。它不证明任意复杂电路、其它 Multisim 版本、
其它厂商模型、PCB 制造、容差/温漂/热/安全或商业版的高级排线与跨板参数优化。

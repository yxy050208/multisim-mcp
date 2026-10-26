# 已审核有源信号链的双板原生验收

本轮把已审核的 `signal-active` 理想电路接入固定双板流程。范围是 Multisim 14.3、
显式 OPAMP5（有限开环增益 100000）、单向缓冲输出、DC/AC 与明确的实验夹具。
它是基本多板联调能力，不包含跨板联合参数搜索、高级布局优化或任意双向耦合仿真。

## 电路与接口

- `sensor`：输入源/源阻抗、串联保护电阻、一阶 RC、缓冲器、供电与去耦。
- `conditioner`：增益/偏置电阻网络、第二级理想运放、一个 100 kΩ 输出负载。
- 两块板各有真实的原生 `HDR1X4 J1`：P1=`path_02`、P2=`rail_positive`、P3=0、P4=0。
  两根地线保留为不同物理引脚，保存重开后分别验证。重复的信号/电源 pin 仍拒绝。

接收板独立实验需要前级频率响应，不能直接用固定 `DC ... AC 1` 替代前级滤波器。
`circuit_fragment` 明确复制已审核的前级 IR 电路，保留源阻抗、RC 相位、有限增益和
供电。片段只用作接收板实验夹具；其来源摘要由本流程重新审核并与全部元件逐项绑定，
通用片段中的 `source_compiled_digest` 字段本身不构成审批证明。

所有板归属、pin、元件值、夹具端口和源/负载都由已批准预览重新推导。修改任一项需要
重新生成并审核原方案。超过十个元件的完全固定分区可进入流程，未固定的更大分区
继续拒绝候选枚举。本轮未加入联合优化求解器。

## 公开入口

继续复用现有工具，不增加 MCP 工具数量：

```python
plan_multiboard_engineering_request(
    executable_netlist=preview, netlist_approval=approval
)
run_native_multiboard_acceptance(
    request=None, output_directory="NEW_DIRECTORY",
    executable_netlist=preview, netlist_approval=approval,
    analysis="ac", analysis_options={
        "sweep_type": 0, "num_points": 10,
        "start_frequency": 10, "stop_frequency": 10000
    }, execute=True
)
```

`request=None` 推导固定分区；若传 `request`，它必须与推导结果一致。审批必须包含
显式激励审阅项；AC 必须在夹具中已声明幅度/相位。`execute=False` 不写文件、不启动
COM。输出目录必须为新目录；执行失败只归档本次创建的目录，不写入竞争创建的目录。
本轮原生执行凭据绑定的是网表/夹具；分析由显式执行调用授权，
`simulation_plan_approval_present=False`，不能宣称还验证了另一份仿真计划审批。

运行器与离线复核：

```powershell
$env:PYTHONPATH = 'mcp_server'
$env:PYTHONUTF8 = '1'
$env:MULTISIM_MCP_TEMPLATE_DIR = 'YOUR_LICENSED_LOCAL_PACK'
python tools/run_signal_multiboard_acceptance.py --preview approved-preview.json `
  --approval netlist-approval.json --output NEW_DIRECTORY --analysis ac --execute
python tools/verify_signal_multiboard_evidence.py NEW_DIRECTORY
```

组件包必须包含 HDR1X4、R/C、DC/AC 源、理想运放与探针模板。可从本机已许可的 NI
样例运行 `bootstrap_local_component_pack.py` 生成本地包；不把 NI 派生模板分发到开源包。

## 原生测量结果

2026-10-11，Windows、本机 Multisim 14.3。审批标志沿用合成回归样本，代表测试合同，
不代表使用者对生产工程的审批。四组最终完整验收如下：

| 样例 | 主进程 | 分析 | 要求/结果 | 状态 |
| --- | --- | --- | --- | --- |
| nominal-dc-final64 | Python 3.13 64 位 | OP | 2.5 V 输入 → 1.5 V 输出 | accepted |
| nominal-ac-final | Python 3.12 32 位 | AC | 增益 0.4、1 kHz 一阶低通，10 Hz–10 kHz | accepted |
| gain-positive-ac-final64 | Python 3.13 64 位 | AC | 增益 3、1 kHz 一阶低通，10 Hz–10 kHz | accepted |
| zero-source-dc-terminal64 | Python 3.13 64 位 | OP | 源阻抗 0 Ω，2.5 V 输入 → 1.5 V 输出 | accepted |

每组实际仿真两块带夹具的板和完整参考工程；另外导出两份不含远端夹具的原生板图，
这两份板图只核对保存/重开、引脚、R/C 与模型，不宣称又进行了孤立板仿真。
合计 12 份仿真工程和 8 份额外板图。OP 为每个电压通道 1 点，AC 为每个电压通道
31 个复数点。独立复核计数为 **16 个 OP 通道样本、496 个 AC 复数通道样本**，
包含供电、共享接口和完整参考的重复观测，不能解释成 512 种独立电路。

- 所有保存重开的已声明引脚，包括每个连接器 P1–P4，严格核对通过。
- R/C 以 SI 读回，相对容差 `1e-9`、绝对容差 0；理想运放 VCVS 模型表达式核对通过。
- 板间/完整参考比较每个原生采样点，包含实部和虚部；严格相同采样轴，无插值，
  比较容差 `1e-9 V`。当前这四组对应样本的原生差值为 0。
- 与独立节点解的最大复数误差约 **3.613e-9 V**，门槛 `1e-7 V`。
- 另行核对需求传递函数 `g·Vin+b` 和 `g·AC/(1+jf/fc)`，最大误差约 **3.616e-9 V**。

## 文件与图面

根目录 `signal-acceptance.json` 是本流程的最终判定；`acceptance.json` 只属于底层
逐板/参考门禁。板图导出或独立目标核对失败时，最终状态仍为 failed。

- `sensor/`、`conditioner/`：带夹具和探针的实验 `.ms14`、PNG、原始测量矩阵、
  逐点 CSV、保存重开报告、R/C/模型读回。
- `full-reference/`：完整已批准电路的同类原生证据。
- `physical/{sensor,conditioner}/`：不含接收板前级夹具的 `.ms14`、PNG、IR、SPICE 和组件 CSV。
- 根目录：审批/需求/来源合同、Markdown/HTML 报告、所有产物 SHA-256 清单。

这里的组件清单包含理想源/理想器件，不是可采购、可投产的 BOM。物理板目录是电路
归属和接口的设计视图，没有 PCB 封装、布局布线、制板文件或实物验收。

图面通过现有基础几何门槛，仍有较多长线与交叉：默认独立接收板为 21 根线、13 个
异网交叉（0.619/线）；带前级夹具的接收板为 37 根线、43 个交叉（1.162/线）。
已实际查看 PNG，不把门槛通过描述为专业绘图质量。高级布局/排线优化保持商业边界。

## 本轮发现与修复

1. 旧本地包缺少 HDR1X4 模板，首次执行在生成阶段失败。生成新的本地许可组件包
   后继续，未覆盖旧包、未提交 NI 资产。
2. 零源阻抗完整参考的理想运放输出探针在保存重开后被 Multisim 遗漏。已将理想运放
   输出探针改为优先端点附着，并在板级及完整参考仿真前验证所有请求电压输出均存在。
   原失败工程保留；修正后新目录原生验收通过。
3. 离线复核不应使用当天组件包猜测历史电压源 pin 顺序。DC/AC 载体编号不同，
   改为核对归档的载体终端合同及源节点集合，再用真实 OP/AC 验证极性/相位。

证据在本地忽略目录 `release/signal-two-board-20261011/`。复核器不启动 COM，核对
哈希、完整审批、固定分区、全部引脚、数值和模型、原始矩阵到归档采样序列的一致性，
并重新计算板间参考差异、独立节点解和需求传递函数。离线通过不是当前机器新实测。
下一步优先给这个受限模型建立更多需求变体与故障注入回归，再接真实运放型号和
受限模拟/数字接口。多版本支持仍需要每个版本独立映射与原生实测。

## 需求变体与归档故障回归（2026-10-11）

新增公开的 `signal_multiboard_regression` 矩阵描述六组有界需求：标称增益、零源阻抗
与 `+90°` 相位、增益 3 与 `−45°` 相位、负偏置、单位增益零偏置，以及小增益与
`−90°` 相位。每组均先用同一审批合同重建前置网表，再分别执行 OP 和 AC；矩阵运行器
不创建审批，也不把测试凭据解释成用户生产审批。

在本机 Multisim 14.3、Python 3.13 64 位主进程上，六组共 12 个分析全部为
`accepted/native-verified`。每个分析包含 sensor、conditioner 和 full-reference 三个
原生工程，保存重开后核对 HDR1X4 四针、阻容、理想模型和完整观测集合，并逐点比较实部、
虚部和需求传递函数。归档复核汇总为 36 个原生工程、24 份独立板图、48 个 OP 通道样本、
1488 个 AC 复数通道样本；最大独立复数误差 `6.60e-8 V`，最大传递目标误差
`6.60e-8 V`，门槛为 `1e-7 V`。样本包含共享接口和完整参考的重复观测，不等于独立电路数量。

故障回归拒绝了 7/7 个故意损坏的归档：文件哈希改变、哈希清单漏项、观测缺失、原始
虚部篡改、阻容读回改变、理想模型改变和夹具端口改变。比较器现在要求恰好一份 sensor、
一份 conditioner 和一份 full-reference，三者各自具备完整且不别名的观测映射，并重新解码
采样状态、分析类型、输出身份、采样轴和有限数值；非数值阻容读回也会以可序列化的明确
失败记录返回。该矩阵仍只覆盖固定理想 OPAMP5 与显式单向前级夹具，不推导真实器件、
任意双向联调、PCB 或高级布线质量。

矩阵只接受外部准备的 `preview`/`approval` JSON，不在运行器中自动批准：

```powershell
$env:PYTHONPATH = 'mcp_server'
$env:MULTISIM_MCP_TEMPLATE_DIR = 'YOUR_LICENSED_LOCAL_PACK'
python tools/run_signal_multiboard_regression.py --inputs INPUTS.json `
  --output NEW_DIRECTORY --execute
```

使用 `--list` 可查看六组需求。执行前所有输入会完整预检；任一案例的审批、夹具或
需求不匹配时不会启动本轮原生矩阵。输入清单、运行日志和本地证据不进入公开仓库。

## 软件与交付检查

- 32 位 Python 全量：1064 项运行，1019 通过，45 跳过。
- 此后增加中间频点虚部故障注入测试；32 位最终定向 33 项通过。
- 64 位最终定向：132 项运行，131 通过，1 跳过。
- wheel/sdist 构建与公开资产检查通过；64 位隔离 wheel 安装后复核四组归档通过。
- 发布审计：0 错误、1 条未提交工作区提示。MCP 工具总数保持 115。
- 本轮只在本地开发和构建，未提交、推送或发布；本地包版本仍为 1.3.0rc3，
  不覆盖已发布的同版本，正式发布需要另选新版本并重跑发布门禁。

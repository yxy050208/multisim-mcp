# 有源信号调理编译与验收边界

`signal-active` 现在可以进入与无源方案相同的草案、元件解析、元件审批、
`CircuitDesign`/SPICE 预览和网表审批链。此模板实现输入一阶 RC、理想运放缓冲、
电阻加权增益/偏置和理想输出驱动，不声明真实 ADC、仪表放大器或任意有源滤波拓扑。
它不调用模型、不写文件、不连接 Multisim、不开始实验。

## 一组可复现输入

选择 `signal-active` 后，将以下 SI 参数交给 `prepare_design_specification`：

```json
{
  "supply_voltage_v": 5,
  "input_min_v": 1,
  "input_max_v": 3,
  "output_min_v": 0.2,
  "output_max_v": 4.2,
  "source_impedance_ohm": 100,
  "load_impedance_ohm": 100000,
  "cutoff_frequency_hz": 1000
}
```

目标是 `Vout = 2 × Vin − 1.8 V`，同时实现 1 kHz 一阶低通。
规格审阅通过后生成逻辑草案，再提交以下显式元件选择给 `resolve_component_requirements`：

```json
{
  "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
  "cr-02": {"family": "ideal-opamp5"},
  "cr-03": {"family": "precision-resistor-network", "voltage_rating_v": 10},
  "cr-04": {"family": "ideal-opamp5"},
  "cr-05": {"family": "ceramic-capacitor", "voltage_rating_v": 10},
  "cr-06": {"family": "capacitor", "voltage_rating_v": 10}
}
```

`cr-06` 是新增的输入滤波电容审查项，不能用运放的元件审批代替电容额定值审查。
已有 `rail-to-rail-op-amp`、`instrumentation-amplifier` 和 `op-amp-buffer` 候选保留，
不会自动改成理想器件。选择真实运放仍要求模型来源；即使提供来源，目前这个模板也会
因器件族不符而拒绝编译。`ideal-opamp5` 拒绝真实型号、外部模型和物理额定值声明。
10 V 只是此例的审查输入，不是采购推荐或对器件数据手册的核验。

经显式元件审阅后，将完整草案与 `approve_component_resolution` 的凭证交给
`compile_executable_netlist`。审阅输出的引脚、数值和模型后，再调用
`approve_executable_netlist`。MCP 与本机 API 使用同一实现；步骤详见
[设计规划](DESIGN_PLANNING.md)。示例不包含任何自动替用户确认的执行入口。

## 电气合同

- 输入、输出范围必须递增，且位于声明的 `0…VCC` 范围内；单电源、正增益。
- 支持增益 `1e-6…100 V/V`；每个计算电阻必须在 `1 mΩ…1 TΩ`，电容在 `1 fF…1 F`。
  超出范围明确拒绝；这些是软件模板的边界，不代表真实器件工作范围。
- `path-00` 是外部输入，`path-04` 是输出；地为 `gnd`，电源为 `rail-positive`。
- 外部源必须带声明的源阻抗；`RLOAD` 已包含声明的输出负载。额外负载改变已审阅电路。
- 两只 OPAMP5 的引脚顺序均为 `IN+、IN−、VS+、VS−、OUT`，分别实现输入缓冲和输出级。
  模型合同及其 SHA-256 同时进入元件来源、支持矩阵、IR 和编译摘要。
- 所有值使用 V、Ω、F、Hz 的 SI 数值，不将 `mV` 字符串当作 V 数字，不自动做 E24 舍入。

原生 OPAMP5 的现有模型是相对于地的有限增益 VCVS：
`Vout = 100000 × (V+ − V−)`。供电引脚有连接，但该模型没有饱和、带宽、压摆率、
电流限制或噪声，也不能证明实物能直接采用这套电阻。供电偏置跟随 VCC，未实现精密
基准。串联保护电阻不等于浪涌钳位；理想输出级加电阻负载不等于 ADC 采样及充电桶。
SPICE 预览使用 Multisim MCP 原生载体方言；在其他 SPICE 引擎中需明确提供兼容模型。

## 为什么能同时处理放大、衰减和负偏置

设目标 `y = g·x + b`，开环增益为 `A`，缓冲增益为 `B = A/(A+1)`。
选择 `k = 2(1 + g/B + |b|/VCC)`。正端的信号、电源、地电导权重分别为
`g/(kB)`、`max(b,0)/(kVCC)` 和剩余权重；负端的反馈、电源、地权重分别为
`1/k − 1/A`、`max(−b,0)/(kVCC)` 和剩余权重。每端总电导固定为 `1/10000 Ω`。

这样有限增益方程给出 `y = g·x + b`；负偏置通过负端的电源电阻实现，电阻的加载已
进入方程。输入 RC 的时间常数包含外部源阻抗，输出负载与 RC 之间由理想缓冲隔离。
它是受限确定性拓扑，不是自动搜索任意电路或真实芯片选型算法。

## 软件验证与原生实验

```powershell
$env:PYTHONPATH = 'mcp_server'
python -m unittest tests.test_active_signal_compiler -v
```

回归使用独立节点方程求解器，从实际生成的网表解析电路，再在测试副本上加入外部
DC/AC 源和源阻抗。九组输入/负载/增益变体检查五个工作点，三组变体各检查五个频率的
幅度和相位；不是仅把合成公式重新算一遍。原预览和审批摘要没有被测试激励修改。
同时检查模型来源、额定值门禁、JSON 往返、供电/模块端口、交换引脚、改变数值/模型、
支持元数据污染，以及旧无源预览摘要兼容性。MCP 与 HTTP 链也有离线回归。
小数端点先用十进制乘积计算偏置，避免 `0.1…0.3 V → 0.3…0.9 V` 的零偏置需求
被浮点误差误判为需要超大电阻；真实的小偏置仍按组件数值边界审查，不会被静默抹除。

上述计算属于软件证据。现已新增 `compile_executable_netlist(..., experiment_fixture=...)`
显式 DC/AC 夹具合同，以及既有 `run_generated_analog_project` 的审批网表绑定。
四组模板与默认方案的两端点完成 Multisim 14.3 保存重开、全引脚/阻容/模型回读、
原生 OP/AC 与导出；64 位主进程也通过一次实机运行。
使用方式、样本数据和图面边界见 [本轮原生验收](ACTIVE_SIGNAL_NATIVE_ACCEPTANCE_20261011.md)。
基础未加激励预览仍标记 `unverified-for-this-template`，原生证据仅覆盖明确的夹具案例。
直接在已批准的 SPICE 后追加源会改变摘要，不能沿用旧网表凭证。
后续推进固定双板传感器示范；高级布局/排线及跨板整体优化保留在商业范围。

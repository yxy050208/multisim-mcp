# D 触发器行为级参考实验

这个示例验证显式行为级参考流程，不宣称 ngspice 模型与 Multisim 原生
`7474N/74LS74` 的传播延迟、阈值、负载能力或电气特性等价。

`native-dff.cir` 使用原生载体引脚顺序：

```text
D, ~PR, ~CLR, CLK, Q, ~Q, GND, VCC
```

运行：

```powershell
multisim-mcp behavioral-reference `
  --netlist .\examples\behavioral-reference\native-dff.cir `
  --commands .\examples\behavioral-reference\reference-tran.txt `
  --output C:\msre_exp\dff-behavioral-reference-ngspice47 `
  --json
```

工具会显式转换为 `@DFF`，固定选择 ngspice，并返回参考网表、波形数据与
`digital_observation` 证据。它不会覆盖输入网表，也不会静默切换后端。

## English summary

This is a reproducible behavioral-reference example for a supported native DFF
carrier. It explicitly converts the carrier to `@DFF`, runs ngspice, and retains
waveform plus digital-observation evidence. It does not claim native 7474N/74LS74
timing or electrical equivalence.

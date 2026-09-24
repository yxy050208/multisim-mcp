# 可移植元件适配器 / Portable component adapters

Multisim MCP 的公开适配器不会携带 NI 数据库或从安装目录提取的 XML。适配器把一个
`@KIND` 伪元件展开为普通 SPICE/XSPICE 原语，因此同一输入既能生成可编辑 Multisim
原理图，也能交给命令引擎实验。

```spice
XT1 p1 p2 s1 s2 @TRANSFORMER LP=10m LS=2.5m K=.995
XP1 vdd wiper 0 @POTENTIOMETER R=10k POSITION=.4
XR1 coil 0 contact_a contact_b @RELAY RCOIL=400 VON=3 VOFF=1
XC1 xin 0 @CRYSTAL RM=20 LM=20m CM=20f C0=3p
XD1 anode cathode @POWER_DIODE BV=200
XM1 drain gate source @POWER_NMOS VTO=4 KP=8
.end
```

数字与混合信号宏采用相同语法：

```spice
XDFF d clk set reset q qbar vdd 0 @DFF
XTFF t clk set reset q2 q2bar vdd 0 @TFF
XCNT clk reset q0 q1 q2 q3 vdd 0 @COUNTER4
XSR data clk reset s0 s1 s2 s3 vdd 0 @SHIFT_REGISTER4
XADC analog digital vdd 0 @ADC1 THRESHOLD=.5
XDAC digital analog_out vdd 0 @DAC1
```

调用 MCP 工具 `component_adapter_catalog` 可取得当前版本、端子顺序、参数边界和完整
示例语法。参数接受有限十进制数、科学计数法及常见 SPICE 后缀。宏名称、节点、参数和
展开行均有长度与字符限制。

## 社区适配器接口

将一个或多个 schema v1 JSON 文件放入独立目录，然后设置：

```powershell
$env:MULTISIM_MCP_ADAPTER_DIR = 'C:\MultisimMcp\adapters'
```

格式见 [`component-adapter.example.json`](component-adapter.example.json)。社区适配器只能：

- 声明 1–32 个端子；
- 声明带默认值、最小值、最大值的有限数字参数；
- 展开为 `R/C/L/K/S/D/M/A/B` 原语或 `.model`；
- 使用已声明的 `{stem}`、端子和参数占位符。

接口不执行 Python、命令行或模板脚本，也不允许 `.include`、`.control`、文件模型或
其他外部文件指令。用户目录中的适配器不能覆盖内置 kind。

## English summary

`Xname nodes... @KIND KEY=value` adapters are declarative macros that expand to
portable SPICE/XSPICE primitives. They do not redistribute NI assets. A local
community pack can add bounded numeric macros through schema-v1 JSON; it cannot
execute code, include files, or replace built-ins.

# HDR1X4 原生连接器验收记录

## 范围

本记录只覆盖 Multisim 14.3 和用户本机由授权样例派生的本地模板包。仓库不
携带 NI 安装文件、样例工程或解码后的模板；可复现实验需要在目标机器上运行
`tools/bootstrap_local_component_pack.py`。

## 元件证据

- 样例：`Getting Started/Getting Started 1.ms14`
- 实例：`J1`
- 原生值：`HDR1X4`
- 引脚：`P1`、`P2`、`P3`、`P4`，编号连续为 1–4
- 每个引脚同时存在 `CIITPinSymbolComp`、`CIITPinConnectorComp` 和 `CiPort`
- 元件没有 SPICE 模型；它的有效性由原生符号、端子和网表连接决定

## 闭环

使用本地派生模板生成以下结构：

```text
XJ1 a b c d HDR1X4
V1 a 0 DC 5
R1 a 0 1k
R2 b 0 2k
R3 c 0 3k
R4 d 0 4k
```

在 Multisim 14.3 中完成：

1. `.ms14` 编码后打开成功，元件枚举包含 `J1`。
2. 保存并重新打开成功，元件枚举仍包含 `J1`。
3. `ReportNetlist` 保留四个连接：`J1/P1→a`、`J1/P2→b`、`J1/P3→c`、`J1/P4→d`。
4. 带四个电压探针的版本在保存、重开后仍枚举全部输出通道。
5. 原生 DC、AC、TRAN 均返回 `ready=true`，没有组件丢失或仿真错误。

证据 JSON 保存在本机目录：
`C:\Users\18331\Documents\multisim-evidence\connector-scan\hdr1x4-native-acceptance.json`。

## 使用边界

兼容清单中的 `connector:HDR1X4` 现在可以在 Multisim 14.3 精确匹配四针签名，
不会再退化为通用 `XSUB2`。其他版本仍需各自提取模板并单独验收；其他连接器
型号即使同为四针，也必须提供自己的 part 标识、引脚签名和版本证据。

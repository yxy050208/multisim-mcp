# 兼容性矩阵 / Compatibility matrix

本表区分“协议可发现”“无 COM 测试”“真实 Multisim 打开/回导”“真实仿真”四个层级。
`derived` 表示公开宏展开为已支持原语；它不等于 Multisim 数据库中的专用外观元件。
数字器件的端口契约同时记录在 `compatibility/components-14.3.json`，并由复杂数字
回归矩阵验证 `ReportNetlist` 信号行和重开 XML 的 `CiNode` 映射。

| 能力 | Windows 32-bit + Multisim 14.3 | Windows 64-bit | Linux/Docker | 证据状态 |
| --- | --- | --- | --- | --- |
| MCP 2026-07-28 / legacy 初始化 | 支持 | 支持 | 支持 | 协议回归 |
| COM 自动化与 `.ms14` 生成 | 支持 | 支持，使用独立 32 位 worker | 不支持 | 真实回归 |
| 安全 SPICE 工作点/DC/AC/瞬态 | 支持 | 支持，使用独立 32 位 worker | 不支持 | 真实回归 |
| RLC、源、半导体、受控源、传输线 | 支持 | 支持，使用独立 32 位 worker | 仅解析 | 真实回归 |
| NOT/AND/OR/NAND/NOR/XOR/XNOR/JK | 支持 | 支持，使用独立 32 位 worker | 仅展开 | 真实时序回归 |
| XFG 函数发生器 / XSC 示波器 | 支持 | 支持，使用独立 32 位 worker | 仅解析 | 真实仪器回归 |
| 变压器/电位器/继电器/晶振宏 | derived | derived | 仅展开 | 14.3 打开/回导；真实瞬态/工作点/AC 回归 |
| 功率二极管/NMOS/PMOS 宏 | derived | derived | 仅展开 | 14.3 打开/回导；二极管/NMOS 工作点回归；需按实物校准 |
| D/T、COUNTER4、SHIFT_REGISTER4 | derived | derived | 仅展开 | 14.3 打开/回导；DFF 瞬态回归；5 V XSPICE 桥 |
| ADC1/DAC1 | derived | derived | 仅展开 | 14.3 打开/回导；单比特行为模型 |
| ADC4/DAC4 | derived | derived | 仅展开 | 14.3 原生重开、引脚和瞬态已验证；真实多位器件模型仍待校准 |
| 数据万用表/Bode/逻辑分析仪 | 支持 | 支持 | 支持已有 raw 文件 | 无 COM 数值回归 |
| 中英 HTML/PDF 与 `manifest.json` | 支持 | 支持 | 支持已有实验目录 | 无 COM 产物回归 |

注意：ASCII raw 解析器同时支持 `real` 和 `complex` 数据。Bode 适配器从复数输出/输入
传递函数计算幅频、相位、峰值和 -3 dB 交点；只有实值列时返回
`phase_available=false`，不会猜测相位。通用 `rows`/CSV 对复数数据使用幅值，解析结果
另保留实部、虚部与相位供 Bode 等复数感知工具使用。中文 PDF 使用
标准 CJK CID 字体描述，极少数缺少 CJK 字体资源的查看器可能需要安装中文字体。

## Release gate

标记为“真实门槛进行中”的适配器在完成 Multisim 14.3 打开、反向网表和仿真回归前
保持 `portable-model`/实验性状态。兼容性问题请附 Multisim 版本、Python 位数、去隐私
后的 `manifest.json` 和最小网表，不要上传授权模板或 NI 安装文件。

## English summary

The table separates protocol discovery, COM-free validation, native open/export,
and real simulation. Portable adapters are derived SPICE models rather than NI
database parts. Missing Bode phase data remains explicitly unavailable.

Digital mappings are versioned in the component manifest. A Multisim version without
an exact verified manifest is rejected by the automatic mapping resolver; it is not
silently treated as compatible with 14.3.

## Version-scoped native evidence

Use the 32-bit Python worker to capture the installed Automation API and compare it
with the versions that need support:

```powershell
py -3.12-32 tools/run_compatibility_matrix.py `
  --target-version "Multisim 14.3" `
  --target-version "Multisim 14.2" `
  --output C:\Temp\multisim-compatibility
```

The tool creates an unsaved blank circuit, writes `native-api.json`, and records
`matrix.json` plus a SHA-256 `manifest.json`. `api-verified` means that the requested
version matches the installed version and its type library was inspected. It does
not certify every component family or complete circuit flow. A known version without
its own probe remains `unverified`; an unrecognized version is `unsupported`.

The 2026-10-03 local probe detected Multisim 14.3. The 14.3 entry is
`api-verified`; 14.2 remains `unverified` until that version is installed and probed.
The evidence is retained locally and does not include NI templates or installation
files.

## 2026-10-16 version gate refresh

The local 32-bit worker detected `Multisim 14.3`. The refreshed matrix requested
three exact targets:

| Requested version | Status | Meaning |
| --- | --- | --- |
| `Multisim 14.3` | `api-verified` | Native Automation API probe matched the installed version |
| `Multisim 14.2` | `unverified` | No 14.2 probe or component manifest is available |
| `Multisim 14.3.1` | `unverified` | Patch-level drift is not borrowed from 14.3 |

The machine reported 32-bit Python and Multisim 14.3. The evidence directory is
`release/multisim-compatibility-20261016-v2`; its `matrix.json`, `native-api.json`,
and `runtime.json` are bound by a SHA-256 `manifest.json`. This proves the API
surface probe only. It does not certify component mappings, schematic round-trip,
or transient behavior for 14.2 or 14.3.1. Those versions require their own
licensed installation, extracted templates, exact manifest, and native regression
before being marked verified.

## Component mapping matrix

The component layer now exposes a separate exact-version mapping matrix. It records
the manifest SHA-256, logical family, native name, pin signature, model source and
per-entry `native-verified`/`native-unverified` status. It never copies a mapping from
a neighboring version:

```powershell
$env:PYTHONPATH = '.;mcp_server'
python tools/build_component_mapping_matrix.py `
  --target-version 14.3 `
  --target-version 14.2 `
  --target-version 14.3.1 `
  --output C:\Temp\component-mapping-matrix.json
```

The current source matrix contains an exact `14.3` manifest with 15 component
entries, 14 marked `native-verified`; `14.2` and `14.3.1` remain
`manifest-unavailable` with empty mappings. This is a manifest evidence report,
not a claim that the current machine can execute every listed component.

# 2N3904 共射原生验收记录

## 本次真正完成的闭环

`plan_natural_common_emitter` 与 `run_natural_common_emitter` 现已连接同一个
厂商模型原生采样执行器。验收需求为 12V 单电源、1kHz 增益 10 倍，固定
100kΩ 负载和 1mV 脉冲输入。本地 Multisim 14.3、32 位 Python、MCP stdio
实际调用成功，原生工程导出后检查了截图。没有修改导出后的 `.ms14` 来伪造显示值。

| 指标 | 原生结果 |
| --- | ---: |
| VCC | 12V |
| 集电极电压 | 6.074946V |
| VBE | 0.663779V |
| 集电极减基极电压 | 4.874108V |
| 1kHz 增益幅值 | 9.890355 |
| 1kHz 相位 | −179.8213° |
| 脉冲输入平台 | 1mV |
| 1.1–1.2ms 输出平台 | −9.8674 至 −9.8515mV |

OP/AC/TRAN 均核对全部声明的引脚；Q1 的 C/B/E 网络为 collector/base/emitter。
源文件与 Multisim 保存后的 2N3904 模型身份、正文及关联模型指纹一致。检查器明确返回
`reference_applicable=false`，不宣称具备晶体管独立线性参考解。

## 图面修复与失败经验

1. 旧共射网表把 AC 激励加在 VCC，输入端没有信号源；现在电源与输入激励分离。
2. 原先目标增益没有参与元件计算；现在根据带负载的集电极等效电阻计算发射极退化电阻。
3. 原先只核对元件名称，偏置接错也能通过；现在核对规定元件的实际端点和供电方向。
4. 原元件包的 `v_element.xml` 实际是 AC_VOLTAGE，不能把它称为 DC_POWER。
   修改符号 `Output` 字符串会在 Multisim 打开时被元件属性重算。现从授权样例新增
   VDC、VPULSE，正确填写其原生参数，并分别以 1=正端、2=负端连接。
5. QNPN 必须保留关联 CiModel；仅保留符号或模型 ID 占位不构成模型身份验收。
6. 原生探针有 `ShowInfo` 字段。现在关闭信息框，共射图隐藏探针图标但保留原生测量，
   经过三类原生仿真验证。该调整发生在生成阶段，图片、工程和 manifest 同步。
7. 共射专用布局只在元件和网络完全匹配时启用；偏置、RC、RE 和负载竖排，
   电容按信号方向布置。文字反向旋转保持水平阅读，阻值用 53.8k、6.2k 等工程单位。

## 复现

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
python tools/bootstrap_local_component_pack.py --output C:\MultisimMcp\ce-pack
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\ce-pack'
python tools/run_common_emitter_plugin_acceptance.py --output output\ce-native
```

输出目录必须新建。实测目录 `sample_validation/ce_mcp_acceptance_final` 的 39 项
manifest 哈希全部通过，MCP 返回与持久化 `acceptance.json` 完全一致。
本地包、模型正文与 `.ms14` 不属于可无条件再分发的开源资产。

## 验收边界

这证明一个受限共射工作流已经能真实建图和仿真，不代表第二阶段全部完成。
增益只验收 1kHz 点，脉冲只验收声明窗口；尚未覆盖完整频带、谐波失真、最大无失真
摆幅、容差、温度或热设计。没有认证其他 Multisim 版本或 64 位端到端运行。
自动图面检查仅覆盖原生源身份/参数、探针显示和几何预检；每个新工程仍返回
`delivery_status=requires-visual-review`，本次样例已另行查看原生截图。

当前共射入口基于已验证的网表执行链路，尚未迁移到完整 CIR。现有 CIR 仍需完善
引脚语义与校验；不应延续此前“全部模板已完成 CIR 迁移”的表述。

## 测试记录

全量运行覆盖 780 项时发现旧 MCP 工具数常量及旧源载体测试兼容问题，均已修复。
后一次全量运行中的 MCP 测试文件缩进错误也已修复，该模块 4 项单独复测通过；
其余模块在全量日志中没有失败。最后的晶体管、原生载体、厂商模型及组合模拟
回归 36 项全部通过，本地 Harness 发布预检通过。日志保留在
`sample_validation/ce_full_regression_final.log` 与 `sample_validation/ce_mcp_regression.log`。
不把含历史失败的全量日志描述为一次全绿运行。

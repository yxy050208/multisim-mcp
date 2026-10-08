# 多板原生验证：电源板＋分压采样板

本次验证使用本机授权 **Multisim 14.3**、32 位 Python COM worker 和本地模板包。
证据目录在源码仓库之外：

`C:\path\to\multisim-evidence\multiboard-fixture-divider-20261005-v8`

## 验证流程

1. 从一个三元件逻辑设计固定分配出 `power` 与 `signal` 两块板。
2. 为跨板 `bus`、`0` 显式声明观测、地参考和边界激励；电源板的单端 `bus`
   额外声明 `RFIX1=1GΩ` 终端，提供可绘制的导线锚点。
3. 为每块板生成独立 SPICE 预览和 `.ms14`，用 Multisim 打开、保存、重开。
4. 通过 `ReportNetlist` 回读组件、网络和引脚连接，执行布局门禁。
5. 在保存重开的工程上用 Multisim 原生 COM 执行 OP；比较两板 `bus` 端点读数。
6. 对完整三元件电路生成独立保存重开参考工程，比较 `bus` 和 `sense` 读数。

## 结果

| 板 | 原生组件 | 探针 | 拓扑 | 布局 | OP | 读数 |
| --- | --- | --- | --- | --- | --- | --- |
| `power` | `V1`, `RFIX1` | 已发出 | pass | pass | native OP pass | `V(bus)=5.0 V` |
| `signal` | `R1`, `R2`, `VFIX1` | 已发出 | pass | pass | native OP pass | `V(bus)=5.0 V`, `V(sense)=2.49999999875 V` |

两板 `bus` 读数的最大绝对差为 `0 V`，与保存重开的完整电路参考相比，`bus` 和
`sense` 的最大绝对差也为 `0 V`，比较容差为 `1e-9 V`。这证明了本阶段的
“显式边界夹具→逐板原生工程→保存重开回读→逐板原生 COM 仿真→跨板及整机参考一致性比较”闭环。

机器可读验收记录位于证据目录的 `acceptance.json`。本次运行记录的检测版本为
`Multisim 14.3`，规范化版本元组为 `[14, 3, 0]`；运行器同时接受 `14.3`、
`Multisim 14.3` 和带补丁号的等价版本字符串，不会因 COM 返回前缀而误报版本不匹配。
验收还单独检查 `all_reopened_topology_pass`，它来自保存后重新打开工程的
`ReportNetlist`，不是生成阶段的临时网表。
根目录同时生成 `acceptance-report.md`；每块板的 `native-op.json` 保留原始 COM 响应，
`native-op.csv` 便于表格分析和后续实验报告引用。TRAN/AC 运行分别使用
`native-tran.*` / `native-ac.*` 文件名。

同一验收运行器还在仓库外完成了 TRAN 和 AC 接口验证：

- `multiboard-fixture-divider-20261005-tran`：`accepted/native-verified`，末时刻读数与完整参考一致（旧版标量门禁证据）。
- 旧的 `multiboard-fixture-divider-20261005-ac` 运行使用纯 DC 夹具并产生全零响应；在当前门禁下该结果会被拒绝，不能当作有效 AC 证据，必须改用显式 `DC 0 AC 1` 激励。
- `multiboard-fixture-divider-20261005-ac-excited-v2`：显式 `DC 0 AC 1` 激励，`accepted/native-verified`；末频率幅值为 `power.bus=1.0 V`、`signal.bus=1.0 V`、`signal.sense=0.49999999975 V`，`analysis_information_pass=true`。
- `multiboard-fixture-divider-20261006-series-dc`：兼容旧 DC 调用路径，在新序列门禁下 `accepted/native-verified`。
- `multiboard-fixture-divider-20261006-series-tran` 与 `multiboard-fixture-divider-20261006-series-ac`：在新增完整序列门禁后再次实机通过；TRAN 逐点比较 58 个原生采样点，AC 逐点比较 7 个频率点的复数响应，接口和完整参考的最大差异均为 `0`。

## 证据边界

这只是 Multisim 14.3 安装环境的工程样例证据，不代表 14.2 或其他版本，也不代表
真实 PCB、器件容差、热设计或安全认证。每个版本仍需独立生成兼容性矩阵。报告、工程、
PNG、CSV 和机器可读结果均保存在上述本地证据目录，未提交到 GitHub。

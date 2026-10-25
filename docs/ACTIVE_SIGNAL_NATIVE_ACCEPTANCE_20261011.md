# 有源模板：显式激励与原生验收（2026-10-11）

`signal-active` 已完成带明确 DC/AC 激励的 Multisim 14.3 原生闭环：编译、
网表及夹具审阅、成图、保存重开、全引脚连接核对、阻容值回读、理想运放模板核对、
OP/AC、原理图 PNG 和 HTML 实验报告导出。结论仅覆盖本页列出的理想模型案例。

## 使用合同

在已有元件审批之后，向 `compile_executable_netlist` 提交可选 `experiment_fixture`：

```json
{
  "schema_version": 1,
  "target_version": "14.3",
  "dc_voltage_v": 2.5,
  "source_impedance_ohm": 100,
  "included_load_ohm": 100000,
  "ac_amplitude_v": 1,
  "ac_phase_deg": 0
}
```

全部为 SI 数字，禁止带单位的字符串、布尔值、NaN、无穷值或任意 SPICE 插入。
DC 必须在已审阅输入范围内。源阻抗与已有负载必须与编译合同完全一致。
AC 为线性小信号频域激励，不代表瞬态波形；未声明 AC 时不能执行原生 AC 实验。
本版只接受 `signal-active`、目标版本 `14.3`、DC 与可选 AC，不接受脉冲或外部文件。

组合器增加 `VINPUT` 和非零源阻抗对应的 `RSOURCE`；`RLOAD` 已存在，不会重复增加。
网络名转换为原生入口支持的名称（例如 `path-00 → path_00`、`gnd → 0`），
原始预览、别名表和完整激励合同均保留并绑定摘要。零源阻抗直接驱动输入。

组合后得到新的 `compiled_digest` 和 SPICE 摘要。向 `approve_executable_netlist`
提供原有网表确认项，并在明确审阅后提供 `confirm_fixture=true` 和与预览一致的
`fixture_sha256`。校验时从基础预览和合同重建组合电路；只改元数据、引脚、值、
源表达式或别名都会被拒绝。原网表的审批不能批准组合后的网表。
未传夹具的旧无源/有源编译接口和旧审批格式保持兼容。

## 原生实验入口

调用既有 `run_generated_analog_project`，同时提供：

- `proposal`：`title`、`application`、**完全相同**的 `preview.spice_netlist`、
  `probe_nets`、`experiments` 和明确的 `checks`；
- `executable_netlist`：带夹具的完整预览；
- `netlist_approval`：上述新预览的有效审批；
- 新输出目录，先 `execute=false` 预览，再根据明确执行意图选择 `execute=true`。

例：观测 `fixture_source` 和 `path_04`；实验为 `op` 和 `ac dec 10 10 10000`。
默认方案的 OP 输出可检查 `1.499…1.501 V`；AC 可在 1 kHz 处检查
`path_04/fixture_source` 的幅值是否接近 `0.4/√2`。
零源阻抗时源网络为 `path_00`；请读取组合后的 `synthesis.interface_contract`。
没有 `checks` 的运行只得到独立方程一致性，不能称为需求验收。

此入口使用明确提交的原生 proposal 和 `execute` 授权分析及验收条件，
**不声称具有独立 `approve_simulation_plan` 凭证**。结果与本地证据中的
`approval-provenance.json` 明确记录 `simulation_plan_approval_present=false`。
通用的 `approve_simulation_plan` 仍可绑定完全相同的组合 SPICE 与 `ExperimentSpec`，
但其后续通用入口分别执行成图与命令引擎仿真，不能将它当作保存工程的原生仿真证明。

原生路径拒绝替换源工程、修改参数或模型，拒绝不同的 SPICE，按夹具目标核对版本。
保存重开的 ReportNetlist 逐针核对电路；所有阻容值按相对容差回读，避免小电容被
固定绝对容差掩盖。生成与重开 XML 中两只 OPAMP5 的 VCVS 表达式必须保持
`e%p %tOUT 0 %tIN+ %tIN- 1e5`；再逐采样点与独立节点方程核对。

## 实机数据

本机 Multisim 14.3；默认供电 5 V、源阻抗 100 Ω、负载 100 kΩ、截止频率 1 kHz。

| 案例 | 输入范围 V | 输出范围 V | DC 激励 V | OP 目标 V | 原生结果 |
| --- | --- | --- | ---: | ---: | --- |
| nominal | 0…5 | 0.5…2.5 | 2.5 | 1.5 | OP/AC 通过 |
| nominal 下端点 | 0…5 | 0.5…2.5 | 0 | 0.5 | OP/AC 通过 |
| nominal 上端点 | 0…5 | 0.5…2.5 | 5 | 2.5 | OP/AC 通过 |
| gain-positive | 0…1 | 0.5…3.5 | 0.5 | 2 | OP/AC 通过 |
| negative-bias | 1…3 | 0…4 | 2 | 2 | OP/AC 通过 |
| zero-source（0 Ω） | 0…5 | 0.5…2.5 | 2.5 | 1.5 | OP/AC 通过，64 位主进程调用 32 位 worker |

每次运行观测两个通道：OP 每通道一个点，AC 每通道 31 点（10 Hz…10 kHz）。
共 6 份保存重开的原生工程、12 个 OP 通道值、372 个 AC 复数通道样本。
采样要求包括 OP 目标与 1 kHz 幅度，完整 AC 扫描另外与独立方程核对。
方程核对容差为 `1e-7 V + 1e-4 × |参考电压|`，仅表示数值一致性。
保留 CSV 的离线复核得到最大 OP 绝对误差 `4.801e-9 V`、AC 复数电压误差
`3.613e-9 V`。源码 32 位与隔离安装的 64 位 wheel 对全部保留产物、审批重建、
模型及严格阻容值检查均通过；安装包离线复核没有重新启动 Multisim。

最终软件回归：32 位全量运行 1045 项（1000 通过、45 跳过）；64 位定向运行
93 项（91 通过、2 跳过）。wheel/sdist 内容门禁通过，公开资产审计 0 错误，
仅有工作区尚未提交的警告。构建包仍带 `1.3.0rc3` 元数据，只作本地验证，不覆盖
已公开的 rc3；本轮未提交、推送或发布。

测试审批标志由本地回归脚本合成，证明协议可用和篡改会被拒绝，不能冒充用户审阅。
原始本地记录位于忽略目录 `release/active-fixture-native-20261011/`，含完整预览、
审批、源 SPICE、原生工程、XML、全引脚网表、阻容值、模型摘要、CSV、COM 原始矩阵、
PNG、HTML、acceptance 与 SHA-256 清单。失败的初次运行也保留：未配置本地模板包，
未开始仿真；配置用户已授权安装提取的本地包后通过。NI 模板和模型正文不进入公开包。
能力发现的 `ideal-signal-fixture` 记录引用本页，不表示读取了当前机器的原始产物。
基础未加激励预览仍标记 `unverified-for-this-template`；实际原生证据归于明确的夹具案例。

## 图面与工程边界

上述案例通过已有几何门禁和原生引脚检查，图面仍需人工审阅。
例如放大案例为 33 条线、40 个异网交叉（1.212 次/线，门限 2），长标签影响阅读。
通过几何门禁不能推出图面已经易读、满足所有绘图规范。
本轮没有加入高级布局/排线优化或跨板整体优化。

OPAMP5 是有限增益理想 VCVS，没有轨到轨饱和、带宽、压摆率、电流限制、噪声或
真实器件模型。这些结果不能证明采购器件、实物 PCB、温度角、容差或安全要求通过。
其他 Multisim 版本、任意运放型号及任意拓扑仍须独立验收。
后续以此合同推进固定双板传感器示范与公开接口验收。

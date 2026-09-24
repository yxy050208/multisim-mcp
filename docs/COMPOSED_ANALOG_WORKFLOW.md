# 组合模拟电路原生工作流（2026-09-09 开发验证）

新增 MCP 工具 `run_generated_analog_project`，复用统一网表编译、原生分析和证据导出。两级、四级电路使用同一个运行器，不需要按器件编号新增布局规则。

## 实际使用流程

1. 用户向已连接此 MCP 的 AI Agent 描述应用和数值要求。
2. 宿主模型生成 `proposal`：`title`、`application`、`netlist`、`probe_nets`、`experiments`、`checks`。模型应明确区分真实芯片要求和理想模型验证。
3. 调用工具，先使用 `execute=false` 检查方案契约和器件范围。理想线性模式还检查直流方程是否可解。预览不写文件、不连接 Multisim。
4. 使用新的输出目录调用 `execute=true`。程序生成原生工程，逐引脚核对 Multisim 导出的连接表，再执行 Multisim OP/AC；LM324AJ 可追加 TRAN。仿真结果来自原生工程，独立节点方程只用作理想线性模式的核验。
5. 工具检查每个观测通道的全部复数样本，以及用户声明的采样指标，导出 `.ms14`、完整 PNG、原始矩阵、CSV、HTML 和文件哈希清单。
6. 若 `target-not-met`，宿主 AI 根据测量结果调整参数或拓扑，以新的输出目录重跑。工具不会将成功执行仿真等同于达到设计指标，也不会宣称搜索到全局最优。

此入口支持宿主 AI 编写组合网表；仓库原有 `model_engineering` 内置模型规划器仍主要面向 RC 合同，尚未自动升级为任意复杂电路规划器。不能把 MCP 工具可调用与各家桌面助手已验证适配混为一谈。

## 可复现示例

- `examples/generated-analog/two-stage-lowpass.json`：14 个器件，两级 RC + 两个非反相理想运放，总低频增益约 10，100 kΩ 负载。
- `examples/generated-analog/four-stage-lowpass.json`：24 个器件，四个 RC 级和四个理想运放，总低频增益约 16，使用不同的器件编号。

示例中每种电路设置三个观测通道，AC 扫描为 10 Hz–100 kHz、每十倍频程 40 点，共 161 点。器件数不含地符号和探针。

已安装本地模板和 Multisim 14.3 时，可在仓库根目录使用配置好的 Python 运行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
python -m multisim_mcp.generated_analog_run --proposal examples/generated-analog/four-stage-lowpass.json --output output/four-stage --execute
```

64 位 Python 前端需要通过 `MULTISIM_MCP_WORKER_PYTHON` 指定已安装依赖的 32 位 Python。生成工程仍需要本地授权模板及 codec；此功能不附带 NI 模板，不绕过 Multisim 授权。

## 验收语义

`checks` 中 OP 使用 `quantity=value`；AC 使用 `magnitude` 或 `phase_deg`。提供 `reference_net` 时检查两个电压的比值。AC 的 `frequency_min_hz` 和 `frequency_max_hz` 指定检查区间，区间内**每个实际采样点**必须符合 `min` / `max`，没有采样点的检查失败。

示例中的通带检查：

```json
{
  "analysis": "ac", "net": "out", "reference_net": "in",
  "quantity": "magnitude", "min": 9.9, "max": 10.01,
  "frequency_min_hz": 10, "frequency_max_hz": 100
}
```

- `passed-declared-sampled-requirements`：原生运行、全引脚连接和明确列出的采样指标均通过。`verification_method=independent-linear-reference` 还核对独立线性方程；`native-vendor-sampled` 核对真实模型保存前后身份与正文哈希，不声明独立方程等价。
- `passed-linear-reference-only`：原生运行和线性方程核对通过，但未提供工程指标；不代表满足用户应用要求。
- `target-not-met`：原生运行完成，但参考或指标未通过。
- `failed`：生成、连接预检或原生执行失败，保留已产生的证据。

独立参考容差为 `1e-7 V + 1e-4 × |参考电压|`。比较包含相位信息，不能仅凭首个 AC 实部或曲线幅值判断正确。

## 已修复的根因与后续器件接入经验

1. **布线失败仍返回穿元件路径**：改为障碍搜索；无合法路线时报错。
2. **忽略起终点元件整个身体**：先沿原生引脚线段的方向离开符号，再执行布线。保留嵌套旋转/镜像变换，不能只比较坐标左右或靠近哪条边。
3. **先布电源线占用后续引脚出口**：预留所有其他网络引脚的出线走廊。异网共线重叠会阻止生成或验收。
4. **多端网络的平均位置落入元件**：选择空闲汇合点，并避免与已有异网汇合点或拐点重合。
5. **探针放在最右侧拐点导致 native EnumOutputs 漏通道**：优先选择真实引脚上的导线端点；模拟前仍必须枚举并检查全部输出。
6. **只解析数字引脚**：连接验收现在覆盖 IN+/IN-/VS+/VS-/OUT 等命名引脚，检查缺失、错网、重复行和额外引脚。严格验收在调用求解器前执行。
7. **导出图像仍是 960×720**：Multisim 导出使用 CircPrefs 中的 Sheet Width/Height，仅修改打印 PageWidth/PageHeight 不够。现在同步更新绘图区并预留标签和探针空间。
8. **真实运放名被静默替换成理想源**：生成阶段拒绝未验证的 LM741/LM358 等别名；使用者必须明确选择 OPAMP5/IDEALOPAMP，或另外接入经验证的真实模型。
9. **PNG 扩展名下存放 BMP 数据**：本机 Multisim 类型库确认 `CircuitImagePNG=0`、`CircuitImageJPG=1`、`CircuitImageBMP=2`；默认导出由 2 改为 0。验证导出应检查文件签名，而不只检查文件名存在。

新器件至少需要符号变换、真实引脚线段方向、原生引脚名称/顺序、模型语义以及原生可观测结果的独立证据。某个名称出现在映射 JSON 中不等于该器件已经通过仿真验收。

## 当前边界

通用入口接受最多 64 个 R/C/L、DC/AC 电压源和理想运放，最多 16 个观测网络、2000 个 AC 点。这是输入上限，不是已完成全部规模/拓扑压力测试的承诺。当前真实验收覆盖 14 和 24 个器件的组合电路。

OPAMP5 是开环增益 100000 的理想受控源，忽略供电饱和、带宽、输出电流和噪声。因此不能据此出具 LM741、LM358 或实物电路的达标结论。现在额外接受明确的 LM324AJ 本地宏模型，要求 OP/AC 和每项分析的指标；可使用 `PULSE(low high delay rise fall width period)`，TRAN 的 `quantity=value` 检查必须指定 `time_min_s/time_max_s`。缺少窗口内样本时失败。此模式返回 `reference_applicable=false`、`reference_passed=null`，不会冒充独立线性参考通过。

LM324AJ 使用每个独立封装的 A 单元；没有自动打包到同一片四运放。其他厂商型号、分立半导体、数字混合、开关电源和多板总体优化仍未在此入口验收。LM324M 替换试验失败，不能视为 LM324AJ 的已验证别名。

导线允许有电气上独立的正交交叉，并报告数量；当前仍需人工检查标签与布局可读性。下一步包括分级布局、真实校准电路、容差与温漂、电气额定值验证。实测结果及复现见 [毕业设计插件验收](THESIS_PLUGIN_ACCEPTANCE_20260909.md)。

# 开放 EDA 后端 / Open EDA Backends

Multisim MCP 现在同时注册 `multisim` 与 `ngspice` 两个仿真后端。Multisim 仍是默认值，
所以升级不会改变现有 Windows/COM 工作流；安装 ngspice 后，Linux、macOS 和 Windows
也可以执行安全 SPICE 仿真、完整实验、指标验收、参数/全局优化与自主纠错。

## 安装与选择

Ubuntu/Debian 示例：

```bash
sudo apt-get update
sudo apt-get install ngspice
export MULTISIM_MCP_EXPERIMENT_BACKEND=ngspice
multisim-mcp
```

其他平台可把可执行文件加入 `PATH`，或设置
`MULTISIM_MCP_NGSPICE=/absolute/path/to/ngspice`。`runtime_status` 会报告两个后端的
能力、ngspice 探测结果和完整实验当前选择。非法后端名会失败关闭。

`MULTISIM_MCP_EXPERIMENT_BACKEND` 影响完整实验及复用同一实验服务的验证、比较、参数
优化、全局优化和自主纠错。它不会改变 `create_schematic_from_netlist`：可编辑 `.ms14`
仍只由 Multisim 生成。单次 `run_spice_netlist` 也可以显式传入
`backend="multisim"` 或 `backend="ngspice"`。

便携式数字适配器在两个后端使用不同的表达式方言：Multisim 保留
`if(condition,yes,no)`，ngspice 后端在生成执行网表时转换为官方支持的三元表达式
`(condition ? yes : no)`。这只改变后端执行网表，不改变源网表或 Multisim 路径；因此
`@DFF` 行为级参考、ADC/DAC 桥和逻辑门可以在 ngspice 47 上真实运行。

## 开放实验产物

ngspice 完整实验不会创建伪 `.ms14`。它发布：

- 带元件/网络标签的 `schematic.svg` 拓扑连接图和有效 PNG 预览；
- `backend.json`，明确记录后端、图类型以及“不可编辑”；
- `circuit.cir`、`run.txt`、`run.log`、SPICE3 ASCII `result.raw` 和 `data.csv`；
- 波形 SVG、Markdown、中英 HTML/PDF、复现 manifest、目录 manifest 和可选验收 JSON。
- `spice-compatibility.json`，记录源/执行网表哈希、方言特性、模型来源与许可证状态、
  后端风险及求解器版本证据。

报告与资源注册按后端档案验证文件，不会把连接图描述为厂商可编辑原理图。开放拓扑图
表达连接关系，不等价于传统符号排版，也不支持反向编辑。

## 安全与可复现性

用户命令仍限定为 `op`、`dc`、`ac` 和 `tran`。执行器使用参数数组启动进程，
`shell=False`，通过 `-n` 禁用用户级 ngspice 初始化文件；ASCII raw 控制段由服务器内部
生成，用户不能注入控制脚本或输出路径。输出采用 staging 和原子发布，持久任务可在运行
期间轮询取消并发送心跳。最大点数使用确定性、保留端点的降采样。

为兼容常见原理图网名，执行副本会把器件连接中的 `gnd` / `ground` 映射为 SPICE
节点 `0`；源网表和审批摘要仍保留原始文本。Multisim raw 对带连字符节点可能显示为
`V(path)-03`，验收测量会将其作为 `V(path-03)` 的确定性显示别名处理。

建议一个完整实验只运行一种分析，避免不同引擎对“当前 plot”的选择差异。厂商模型、
专有 code model、求解器默认值和 SPICE 方言可能不兼容；系统不会静默声称等价。

## 跨后端差分

先分别注册 Multisim 与 ngspice 实验，再调用 `compare_experiment_backends`。工具按共同
信号名（大小写不敏感）匹配，在第一列坐标上做线性对齐，返回 MAE、RMSE、最大绝对误差、
归一化 RMSE、容差违例和总判定。无公共信号或无重叠域时明确标记为 `unverified`。

公共 CI 在 Ubuntu 安装真实 ngspice，并验证低层后端和完整开放实验包。本地 Multisim 与
ngspice 的数值差分仍应在同时具备两个运行时的机器上执行；CI 不能替代厂商模型验证。

## English summary

Set `MULTISIM_MCP_EXPERIMENT_BACKEND=ngspice` to run complete experiments and
the existing verification/optimization/correction services through a local
open-source simulator. The Multisim default remains unchanged. The ngspice
path uses the same safe analysis allowlist, no shell, no user init file,
cancellable process polling, ASCII raw output, deterministic downsampling, and
atomic publication. It emits an honest non-editable SVG/PNG connectivity graph
instead of a fake `.ms14`. Use `compare_experiment_backends` for tolerance-based
numerical calibration between registered runs.
Execution decks canonicalize common `gnd` / `ground` node aliases to SPICE node
`0` for Multisim compatibility while preserving the source netlist and approval
digests. Measurement lookup accepts Multisim's `V(path)-03` raw display spelling
as the deterministic alias of canonical `V(path-03)`.
Portable digital adapters retain Multisim's `if(condition,yes,no)` spelling but
are emitted as ngspice's supported ternary expressions in the ngspice execution
deck, so the source netlist and Multisim behavior remain unchanged.
Every new experiment also emits source/executed-netlist hashes and model/solver
evidence in `spice-compatibility.json`; comparisons expose whether the inputs
are actually identical before interpreting numerical differences.

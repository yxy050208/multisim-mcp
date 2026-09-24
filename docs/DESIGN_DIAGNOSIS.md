# 确定性设计诊断 / Deterministic Design Diagnosis

`diagnose_design` 是一个传输无关、只读的第一版工程诊断入口。它不会调用模型、启动
Multisim、执行仿真、修改设计或自动应用修复。目标是先把可复现的事实与启发式提示
结构化，再由工程师、Agent 或后续优化流程决定是否提出补丁和复测。

## 能诊断什么

- 拓扑：缺少参考地、单连接网络、声明但未使用的网络、元件所有引脚短接等；
- 激励与偏置：结构化设计缺少独立电压/电流源；
- 指标：把已有 `verification.json` 的 `fail` 与 `unverified` 明确分开；
- 收敛：识别奇异矩阵、不收敛、时间步过小、GMIN/source stepping 失败和迭代上限；
- 工作点：仅在端电压齐全时，用保守阈值提示 NPN/PNP 截止、反向偏置或可能饱和；
- 运放：仅对引脚顺序为 `+、-、V+、V-、out` 的 `OPAMP5`，提示输出接近电源轨。

所有 finding 都返回稳定 `code`、严重级别、证据、受影响对象、建议检查项和
`auto_fixable=false`。`no_problem_detected` 只表示这些规则未发现问题，不是电气正确性
证明。MOS 工作区不会在缺少阈值和模型证据时推断。

## MCP 使用

只检查设计：

```json
{
  "design": {
    "schema_version": 1,
    "design_id": "divider",
    "title": "10 V divider",
    "revision": 0,
    "components": [],
    "parameters": {},
    "annotations": {}
  }
}
```

附加完成实验和失败信息：

```json
{
  "design": { "schema_version": 1, "design_id": "divider", "title": "divider", "revision": 0, "components": [], "parameters": {}, "annotations": {} },
  "experiment_dir": "C:\\experiments\\divider-op",
  "simulation_failure": {
    "stage": "operating-point",
    "code": "solver-error",
    "message": "singular matrix"
  }
}
```

`simulation_failure` 只接受 `code`、`type`、`stage`、`message`。失败信息是诊断证据，
不是执行命令。

## CLI 使用

```powershell
multisim-mcp diagnose-design --design .\design.json --json

multisim-mcp diagnose-design `
  --design .\design.json `
  --experiment .\completed-experiment `
  --failure .\simulation-failure.json `
  --json
```

CLI 成功完成诊断时退出码为 0，即使 findings 中含有 `error`；输入、完整性或 schema
错误返回 2。自动化流程应读取 `overall_status`，不要把命令执行成功误解为设计通过。

## 实验证据门禁

实验目录必须满足以下条件：

1. 不是符号链接，并存在可递归校验的 `directory.manifest.json`；
2. manifest 类型为 `experiment`，状态为 `succeeded`；
3. `circuit.cir` 存在、受大小限制，且其规范化内容与输入 `CircuitDesign` 生成的网表一致；
4. 可选 `verification.json`、`result.raw`、`run.log` 都受固定大小限制且不能是符号链接；
5. RAW 只有一个点或明确为 operating point 时，端电压才进入器件工作区诊断。

这比模型侧 `model-diagnose --experiment-dir` 的用户声明式关联更严格。两条入口用途不同：
先用 `diagnose_design` 获得本机确定性事实，需要自然语言推理、解释或补丁提案时，再把
结果交给模型工作流。

## 输出与后续流程

结果明确包含 `read_only=true`、`source_design_modified=false` 和
`simulation_performed=false`。建议的安全顺序是：

1. `diagnose_design` 收集事实；
2. 人工或 `model-diagnose --enable-patch-preview` 提出最小 `DesignPatch`；
3. 用审批绑定的验证流程对内存候选运行新实验；
4. 只有全部要求通过后才持久化；否则保留原设计和诊断证据。

## English summary

`diagnose_design` and the `diagnose-design` CLI provide deterministic, read-only
topology, requirement, convergence, and evidence-backed BJT/op-amp operating
point findings. They do not invoke a model, start COM, run a simulation, mutate
the design, or apply repairs. A completed experiment is accepted only after its
recursive manifest verifies and its canonical netlist matches the supplied
`CircuitDesign`. Findings remain conservative heuristics rather than proof of
electrical correctness; failed and unverified requirements are kept distinct.

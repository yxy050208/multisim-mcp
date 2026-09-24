# 真实运行验证记录 / Real Runtime Validation

本文记录一次在 Windows 授权工作站上的真实运行验证。它不是 CI 的替代品，
也不是对所有 Multisim 版本、元件库和模型的兼容性承诺；它的作用是把“本机
确实跑过”与“跨环境可复现”分开保存。

## 环境

- 操作系统：Windows 11
- 前端/Multisim worker：Python 3.12.10 32-bit，`pywin32` 可用
- Multisim：NI Multisim 14.3，COM `MultisimInterface.MultisimApp` 已注册并可激活
- 编解码器：`electronics-workbench-decoder` EWD/EWE 0.2.0
- 开放仿真器：ngspice 47 x64，通过用户级 `MULTISIM_MCP_NGSPICE` 配置
- MCP：`multisim-mcp` 1.1.0

ngspice 不随仓库分发；安装方式和后端边界见
[`开放 EDA 后端`](OPEN_EDA_BACKENDS.md)。NI 数据库、`.ms14` 模板和专有模型也不
写入公共仓库。

## 已完成的真实检查

### 1. 运行时门诊

```powershell
multisim-mcp doctor --connect --strict --json
```

结果：`success=true`、`activation_ready=true`、`full_workflow_ready=true`，并报告
Multisim 14.3 的实际安装路径。该检查只验证连接、编解码器和静态前置条件，不会
声称任意电路都能收敛。

### 2. Multisim 完整实验

使用一个 10 V 电阻分压器执行 `op`：

- 成功生成可编辑 `circuit.ms14` 和 `schematic.png`；
- Multisim 实际返回 `V(out)=5 V`；
- 生成 `result.raw`、`data.csv`、命令/日志、波形图、Markdown、中文/英文 HTML/PDF、
  SPICE 兼容性审计和实验 manifest；
- 所有产物通过原子发布和哈希登记。

### 3. ngspice 完整实验

同一服务切换到 `MULTISIM_MCP_EXPERIMENT_BACKEND=ngspice` 后，使用安全 `tran` 命令
完成 RC、DFF 行为级参考和五波形课程 Demo：

- DFF 参考实验观察到 `Q`/`~Q` 互补输出，初值分别为 0/1，且均有上升/下降边沿；
- 五波形 Demo 的频率、峰峰值和两路正弦 THD 共 12 项要求全部 `pass`（12/12）；
- 真实运行产生 8,342 个瞬态点，导出 CSV、SPICE3 ASCII raw、拓扑 SVG/PNG、波形 SVG
  和中英双语报告；
- ngspice 执行网表使用后端专用三元条件表达式，源网表仍保持 Multisim 的表达式形式。

### 4. 跨后端差分

同一 RC 网表分别在两个后端注册实验，再调用 `compare_experiment_backends`：

- 两端 `V(out)` 共同时间域比较通过；
- 最大绝对误差约 `9.9e-8 V`，归一化 RMSE 约 `5.3e-5%`；
- 审计正确标记为“执行网表不同、求解器版本证据不完整”，因此数值通过不等价于
  厂商模型或时序行为等价。

### 5. 受控交接包闭环

在临时工程中按“先成图、后仿真”的审批交接路径执行一个 RC smoke test：

- `create_schematic_from_netlist` 成功生成 `.ms14` 和 PNG；
- Multisim 14.3 实际返回 raw/CSV，双语报告、波形和 manifest 全部原子发布；
- `verification.overall_status=pass`，验收项 `1/1`；
- manifest 中的 `approval_provenance` 通过完整性校验，`inspect-project` 将条目标记为
  `verified`；
- 该运行同时验证了 `gnd`/`ground` 执行别名和 Multisim 带连字符节点的 raw 显示别名。

## 回归门禁

在已配置两个真实后端的工作站上执行：

```powershell
$env:PYTHONPATH = '...\\multisim_re\\mcp_server'
$env:MULTISIM_MCP_NGSPICE = '...\\ngspice_con.exe'
$env:MULTISIM_MCP_RUN_REAL_TESTS = '1'
python -m unittest discover -s mcp_server -p 'test_*.py'
```

本次结果：`Ran 578 tests ... OK (skipped=8)`。不设置 `MULTISIM_MCP_RUN_REAL_TESTS` 时，依赖
授权 Multisim 的长耗时门禁会自动跳过；公共 CI 仍应至少运行 Linux ngspice 回归和
协议 introspection。

发布前还通过：

```text
DeepSeek Harness compatibility: PASS
DeepSeek Harness plugin release: PASS
python -m compileall -q mcp_server
git diff --check
```

## 证据边界

这些结果证明当前工作站上的执行链路可用，不证明：

- 行为级 `PULSE`/`PWL`/`SIN` 参考就是 HE555、74LS74、LM324 或 1N4007 的原生模型；
- 任意厂商模型、专有 code model、元件容差、面包板寄生或示波器实测都已覆盖；
- 不同 Multisim 版本、ngspice 构建、操作系统或求解器选项具有相同数值结果；
- 优化器已经得到任意非凸电路的数学全局最优。

课程设计若要提交“元器件级通过”，仍需在有授权的 Multisim 中填充四类模型的来源、
许可证和 SHA-256 证据，并对原生网表重新取得 12/12 验收。行为级结果可作为跨平台
回归和产品演示证据，不能替代该门禁。

## English summary

This record separates a real local runtime gate from a portability claim. On the validated
Windows workstation, Multisim 14.3 COM, the 32-bit worker, codecs, ngspice 47, the complete
experiment pipeline, the DFF behavioral reference, the five-waveform 12/12 contract, and a
cross-backend RC comparison all ran successfully. A controlled-handoff RC smoke test also
completed the schematic-first path with `verification.overall_status=pass`, full artifacts,
and verified approval provenance; the execution deck now canonicalizes common ground aliases
and measurement lookup accepts Multisim's hyphenated-node display spelling. The repository still does not redistribute
NI databases or proprietary models. Behavioral waveform PASS is not evidence of native
HE555/74LS74/LM324/1N4007 behavior; a component-level course submission still needs licensed
Multisim model provenance, hashes, and a fresh native 12/12 verification.

# 厂商 SPICE 宏模型兼容 / Vendor SPICE Macro Models

Multisim MCP 可以在不读取外部文件的前提下，接收直接粘贴在网表中的 `.subckt` 厂商宏模型。实验仍由 Multisim 命令引擎执行；对于可安全展开的模型，自动原理图还会把宏模型递归展开为可编辑的原生器件。

## 工作方式

1. 安全层拒绝 `.include`、文件型 `.lib`、控制脚本和文件数据源。
2. 解析器收集内联 `.subckt`、`.model`、`.param` 和 `.func` 定义。
3. 顶层 `X...` 实例按照子电路声明解析引脚和 `PARAMS:` 参数。
4. 兼容的嵌套子电路递归展开，内部节点使用实例作用域命名，内部参考编号转换为 Multisim 可稳定回导的字母前缀加数字后缀。
5. `.ms14` 打开后仍执行原生网表反向完整性检查；权威实验数据由同一原始网表经 Multisim 命令引擎产生。

构建结果中的 `editable_model_coverage` 明确报告可编辑模型覆盖：

```json
{
  "status": "complete",
  "expanded_instances": 2,
  "carrier_only_instances": 0
}
```

`status` 可以是 `complete`、`partial`、`carrier_only` 或 `not_applicable`。客户端不得把 `partial` 或 `carrier_only` 表述为完整可编辑厂商模型。

## 当前可展开器件

宏模型主体目前可递归展开以下 SPICE 记录：

- R、C、L、独立 V/I 源；
- B/E/F/G/H 受控源；
- D、三端 Q、四端 M、J/Z、S/W；
- T/O/U 和 K 耦合；
- 继续引用内联定义的 `X` 子电路。

支持子电路头部默认参数和实例端 `PARAMS: NAME=value` 覆盖，也会改写 `V(node)`/`I(source)` 表达式中的局部引用。递归深度上限为 16，引脚上限为 16。

## 明确边界

- `.if/.elseif/.else/.endif` 可以通过安全网表验证并由命令引擎执行，但当前不会展开到可编辑原理图；结果标记为 `carrier_only` 或 `partial`。
- 参数表达式当前优先支持无空格的 `NAME=value` 形式；复杂表达式仍以命令引擎结果为准。
- 通用载体符号只表达外部引脚，不证明模型主体已经进入可编辑原理图；必须检查 `editable_model_coverage`。
- `.func` 调用、条件模型块、四端 BJT、厂商专用 A-device、加密模型以及未定义的嵌套子电路暂不展开；这些安全指令仍可由同源命令引擎网表使用。
- 自动生成的原理图探针仍是实验性能力，正式实验数据来自 `run_circuit_experiment` 的命令引擎事务。

## 已验证回归

真实 Multisim 14.3 回归使用两级 LM324 有限增益带宽宏模型。两个 `LM324E` 实例分别展开为 E/R/C/B/R 五个内部器件；生成的 `.ms14` 通过全部内部器件反向检查，同源瞬态实验的 30 kHz 频率、3 Vpp 幅度和 THD 三项要求全部通过。

## English summary

Inline vendor `.subckt` models can be recursively expanded into editable native
primitives while the authoritative simulation continues to run from the same
source netlist through Multisim's command engine. Pin declarations, `PARAMS:`
overrides, nested inline dependencies, local nodes, and expression references
are preserved. The machine-readable `editable_model_coverage` field prevents a
carrier-only symbol from being reported as a fully editable vendor model.

External `.include` and file-backed `.lib` directives remain blocked. Conditional
macro blocks, proprietary devices, and models beyond the documented primitive
subset may still simulate through the command engine but are reported as partial
or carrier-only schematic coverage.

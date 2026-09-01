# SPICE 方言、模型来源与跨后端证据

## 为什么需要这一层

SPICE 不是单一标准实现。相同的 `.model`、行为表达式、条件网表或数字
`A` 器件，在 Multisim、ngspice 以及不同版本的求解器中可能有不同语义。
“能够解析”不等于“两个后端正在计算同一个问题”。因此 Multisim MCP 从
当前版本开始为网表和完整实验生成保守的兼容性档案，不把静态检查冒充
真实跨后端验证。

## 使用方式

可以在运行实验前单独调用 `audit_spice_compatibility`：

```json
{
  "netlist": "V1 in 0 5\nD1 in 0 DMOD\n.model DMOD D(IS=1e-14)\n.end\n",
  "backend": "ngspice",
  "declared_dialect": "ngspice-45",
  "model_references": [
    {
      "name": "model:DMOD",
      "source": "vendor-model-library",
      "sha256": "<sha256 of the embedded definition>",
      "license": "BSD-3-Clause"
    }
  ]
}
```

`model_references` 与 `CircuitDesign.model_references` 使用同一字段：`name`、
`source`、`sha256` 和 `license`。服务器不会读取或下载外部文件；如果模型
没有内联在网表中，档案会明确标记为 `not-embedded`，而不是声称可复现。

## 完整实验的新增产物

每个新实验目录包含 `spice-compatibility.json`，并在
`multisim://experiments/{id}/spice-compatibility` 提供 MCP Resource。它记录：

- 用户源网表和实际执行网表各自的 UTF-8 字节数、行数和 SHA-256；后者可识别
  `@KIND` 适配器或后端准备步骤造成的输入变化；
- 声明或推断的方言、检测到的特性和高风险标记；
- 后端、静态兼容状态以及从 `run.log` 捕获的求解器版本（未捕获会保持
  `null`）；
- 每个内联/外部/未解析模型的来源、定义 SHA-256、许可证状态、引用器件
  和内容是否嵌入；
- 实际执行网表的检测特性和内联模型指纹，用于发现后端准备阶段的模型集合变化；
- 结构化诊断、风险等级、模型指纹和证据完整性摘要。

正式 HTML/PDF、Markdown 报告、`manifest.json`、目录 manifest 和实验摘要
都会引用这份档案。旧实验目录仍可以注册；缺少档案时报告会显示证据未验证。

## 如何理解状态

- `portable-static-subset`：当前网表只落在已识别的低风险静态子集，仍建议
  运行时验证；
- `runtime-verification-required`：检测到行为表达式、条件网表、保护块、
  XSPICE code model 等需要后端运行才能确认的特性；
- `static-errors`：出现模型 SHA-256 不一致等证据错误，不能把结果当作可
  追溯输入；
- `provenance_complete=false` 是有意的保守结果：没有明确许可证、精确方言
  或求解器版本时不会变成 `true`。

## 跨后端比较

`compare_experiment_backends` 仍然返回数值 MAE/RMSE 和容差结论，同时增加
`input_and_solver_evidence`：

- `verified`：网表和模型指纹相同，且两个求解器版本都被捕获；
- `partial`：输入相同，但至少一个求解器版本未捕获；
- `incomparable`：网表或模型不同，数值差异不能解释为纯求解器差异；
- `unverified`：实验生成于审计产物加入之前，或产物缺失/损坏。

该检查不是电气等价证明，也不替代真实 Multisim/厂商模型回归。它的目的
是让开源项目、课程报告和比赛演示中的“可复现”声明有明确边界。

## English summary

`audit_spice_compatibility` and the `spice-compatibility.json` artifact record
the exact netlist hash, inferred or declared dialect, model source/license/hash
evidence, backend risks, and solver-version evidence. Missing model content,
unknown licenses, and missing versions remain explicit gaps. Numerical backend
comparison now reports whether the two runs used byte-identical source and
executed netlists and model fingerprints, so a matching waveform is not
misrepresented as proof of solver equivalence.

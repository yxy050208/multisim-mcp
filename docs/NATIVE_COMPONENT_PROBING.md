# 原生元器件探测 / Native component probing

本页记录课程设计从行为级参考走向原生 Multisim 器件时的安全边界。数据库路径只是
本机 Multisim 14.3 的候选元数据，不包含、也不授权重新发布 NI 或第三方模型。

## 当前本机结果

| 课程器件 | Master Database 候选路径 | 本机结果 | 可否形成元件级声明 |
| --- | --- | --- | --- |
| 1N4007 | `Diodes / DIODE / 1N4007` | 已完成替换、保存、BOM、反向网表和解码身份检查 | 仍需绑定课程原生电路和 12/12 实验 |
| LM324 | `Analog / OPAMP / LM324M` | 已完成替换、保存、A 节、反向网表和解码模型身份检查 | 仍需放入三个课程模块并跑 12/12 |
| 74LS74 | `TTL / 74LS / 74LS74N` 或 `74LS74D` | 精确 74LS 替换仍不兼容；已找到同功能 `74STD / 7474N` 的 8 引脚 A 节载体 `DFF8` | 仅可作有明确替代说明的功能级证据 |
| HE555 | 本机无精确 `HE555` 名称；候选 `Mixed / TIMER / LM555CN` | LM555CN 已在 8 引脚载体上完成替换、保存和身份解码；ReportNetlist 不展开宏体 | 仅在课程允许且证据写明兼容理由后 |

这些结果不会预填 `status=verified`。用户必须从自己的授权安装生成本地产物和 SHA-256，
再填入 `course-component-evidence.template.json`。项目不会把提取的原理图模板、宏模型或
主数据库记录提交到 Git。

当前开发机的用户包位于 `%LOCALAPPDATA%\multisim-mcp\course-native-pack`，已覆盖三个仅限
本机的模板：`OPAMP5 ← LM324M`、`D ← 1N4007`、`TIMER8 ← LM555CN` 和
`DFF8 ← 7474N`。它们的来源文件、安装文件与备份哈希均
记录在该目录的 `local-pack-manifest.json`；这不是仓库资产，也不应上传到 GitHub。

## 将已验证型号覆盖到本地包

`bootstrap_local_component_pack.py` 生成的是可运行的本机包；对已经完成替换、保存和身份
检查的临时 `.ms14`，可以用覆盖工具安装更精确的本地模板。覆盖工具只写入用户指定的包，
先备份原模板，并在 `local-pack-manifest.json` 中记录源文件和安装文件的 SHA-256：

```powershell
python tools/overlay_local_component_pack.py `
  --pack C:\MultisimMcp\component-pack `
  --source C:\path\to\verified-lm324.ms14 `
  --refdes U5 --kind OPAMP5 --identity-token LM324M --force
python tools/overlay_local_component_pack.py `
  --pack C:\MultisimMcp\component-pack `
  --source C:\path\to\verified-1n4007.ms14 `
  --refdes D3 --kind D --identity-token 1N4007 --force
python tools/overlay_local_component_pack.py `
  --pack C:\MultisimMcp\component-pack `
  --source C:\path\to\verified-lm555cn.ms14 `
  --refdes U1 --kind TIMER8 --identity-token LM555CN --force
python tools/overlay_local_component_pack.py `
  --pack C:\MultisimMcp\component-pack `
  --source C:\path\to\verified-7474n.ms14 `
  --refdes U1 --kind DFF8 --identity-token 7474N --force
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\component-pack'
$env:MULTISIM_MCP_TEMPLATE_ONLY = 'true'
```

`refdes` 必须填写探针工程保存后实际出现的引用编号，不能凭课程网表猜测。覆盖不会将
`.ms14`、解码 XML 或模型正文复制进仓库；`overlay-backups/` 和来源文件也应留在本机。
生成器会把模板引用的 `CiModel` 占位项加入工程，避免 Multisim 在原生反向网表中静默
丢失宏模型器件。对于 TIMER8，Multisim 的 `EnumComponents`/节枚举是原生存在证据；
`ReportNetlist` 可能只列出外围连接而不展开供应商宏体，这一差异会记录在验证结果中。
精确型号仍需绑定课程原生电路并通过 12/12 验收后，才能更新证据声明。

为继续缩小 74LS74 缺口，本机样例中的 `74LS273N`（八路 D 触发器）和 `74LS373N`
（八路 D 锁存器）已分别作为载体进行隔离替换；两者对 `74LS74N/74LS74D` 都返回
`worker_crashed`，探针已清理对应的 Multisim 子进程。因此不能把“同属 74LS 家族”误当作
引脚/内部类型兼容，下一步需要找到真正的 D 型双触发器载体或走受控的原生放置接口。

另一个样例 `QuizShowVariants.ms14` 含有 `7474N` 双 D 触发器。提取其 A 节后，生成器以
`DFF8` 暴露 8 个逻辑端子（D、~PR、~CLR、CLK、Q、~Q、GND、VCC），真实 Multisim 回归
已确认 `U1` 可打开并枚举。它是 `74STD/7474N` 功能替代，不是 `74LS74N/74LS74D` 的
精确型号证明；第二个内部触发器也没有实例化。直接命令引擎瞬态可返回 `state=0`，但
当前导出的 raw 变量不包含 `Q/~Q`，因此这只能算打开/枚举证据，不能算输出时序仿真证据。
进一步的 COM 探查显示该工程的 `EnumOutputs(0)`、`EnumOutputs(digital)` 均为空，直接请求
`V(q)`/`V(nq)` 也被 Multisim 拒绝；这不是把缺失列当作零值，而是当前输出采集接口没有
暴露该数字宏的观测点。
结构化设计在经过纠错或优化后重新编译时，会保留为严格 8 端子的 `XU...` 记录；端子数
不匹配会立即失败，避免把载体错误降级成普通子电路。

## 隔离替换探针工具

对未知载体不要直接在主 Multisim 会话调用 `ReplaceComponent`。项目提供隔离探针，默认
使用 Multisim 主数据库枚举值 `0`（不是 typelib 中显示的变量 memid `1073741824`）：

```powershell
python tools/probe_native_replacement.py `
  --source C:\path\to\carrier.ms14 `
  --output C:\path\to\probe-result.ms14 `
  --component U1 --section '' --database 0 `
  --group Mixed --family TIMER --source-name LM555CN --model ''
```

探针在独立 32 位 Python 进程中运行；若 Multisim 因不兼容载体崩溃或超时，只清理本次新
启动且路径核验为 Multisim 的进程，并返回结构化 `worker_crashed`/`timed_out` 结果。

## 为什么替换探测必须隔离

Multisim Automation API 的 `ReplaceComponent` 要求目标元件与载体类型、节名和引脚名称
兼容。错误的数据库路径或不兼容载体在 Multisim 14.3 中可能使 32 位 COM 进程直接退出，
而不是返回普通异常。因此产品实现必须遵守以下门禁：

1. 只在临时副本上探测，不修改用户原文件。
2. 一次候选使用一个隔离工作进程，设置超时并记录退出码。
3. 仅使用 pywin32 生成的强类型 Multisim wrapper；损坏的 `gen_py` 缓存会退回动态
   dispatch，不能用于数据库写操作。
4. 替换后重新枚举元件/节、保存为新文件、导出 BOM 和原生网表，并解码检查准确型号。
5. 只有随后通过同一课程的 12 项真实 Multisim 验收，才把实验和模型证据交给声明门禁。

`component_level_claim` 由产品根据证据计算，调用方不能直接设置。候选路径成功也只说明
模型进入了临时原理图，不说明频率、幅度、THD、容差或实物面包板已经通过。

## 许可证边界

- 可以开源：数据库路径候选、适配代码、散列、验证结果、错误分类和不含模型正文的报告。
- 默认只留本机：由 Master Database 复制出的 `.ms14`、解码 XML、SPICE 模型正文和符号模板。
- 可发布前提：模型自身许可证明确允许再分发，并在 provenance 中记录来源、版本和许可证。

## English summary

Native database replacement is feasible but must run in an isolated local worker. On the current
licensed Multisim 14.3 installation, `1N4007`, `LM324M`, and `LM555CN` were successfully replaced
and round-tripped. The LM555CN carrier is now available locally as `TIMER8`; its native component
enumerates correctly even though `ReportNetlist` does not expand the vendor macro body. `74LS74`
still needs an exact compatible 74LS carrier. A generic `7474N` A-section from the
`QuizShowVariants.ms14` sample is now available locally as `DFF8` and opens/enumerates in a real
Multisim regression, but it is a `74STD` functional substitute rather than exact `74LS74N/74LS74D`
evidence; the second internal flip-flop is not instantiated. The exact `HE555` name is absent;
the current command-engine raw export also omits `Q/~Q`, so no native timing claim is made.
The exact `HE555` name is absent; an LM555 substitution requires explicit course approval and an
electrical-compatibility rationale.
The sample `74LS273N` and `74LS373N` parts were also tested as carriers for `74LS74N/74LS74D`;
both safely returned isolated worker crashes, confirming that same-family membership is not enough
to establish pin/type compatibility.
The DFF8 COM output surface is also empty for both general and digital output enumeration, and
explicit `V(q)`/`V(nq)` requests are rejected; the current limitation is therefore an unavailable
observation point rather than a zero-valued output claim.
No extracted NI or third-party model is redistributed. A successful replacement is never equivalent
to the complete component-level 12/12 course claim.

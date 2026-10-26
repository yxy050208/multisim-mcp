# 单板布局与布线优化

开源版现提供一个有界的单板布局候选评分层。它接收已有的元件位置和一个布局验证器，
对基线及有限的单元格移动候选进行评估，并按以下顺序选择结果：

1. 通过几何门禁的候选优先；
2. 通过功能/电气门禁后，使用归一化的 `electrical_score`（越低越好）；
3. 没有回退导线的候选优先；回退导线候选不能进入最终应用；
4. 异网交叉率更低的候选优先；
5. 导线弯折数和总长度更少的候选优先；
6. 元件整体排列跨度更小的候选优先；
7. 错误数更少的候选优先；
8. 候选 ID 作为最后的确定性排序键。

“简洁”只在功能和性能门禁通过后生效。布局 evaluator 可以附带
`functional_status` 或 `electrical_status`；非 `pass`/`accepted`/`native-verified`
状态、元件重叠、断线或拓扑不一致的候选必须先被淘汰，不能用更短的导线换取错误的工程。
参数优化器可以把已验证的电气目标误差传成 `electrical_score`，从而在同一候选集内先比较
电气结果，再比较布局可读性。
多候选接入时可使用 `rank_electrical_layout_candidates`，每个候选提供
`electrical_status`、`electrical_score` 和 `layout_result`，电气失败会在布局排序前被淘汰。
对于已有的 `DesignOptimizationService` 结果，可用
`rank_optimization_directory_candidates` 读取当前的 `optimization.json`（并兼容旧的
`optimization-state.json`），按
`evaluation_id` 注入材料化布局结果；缺少布局证据的参数候选会自动失败关闭。

现在还可以直接对一个已完成的参数优化目录执行自动联调：
`design_optimization.evaluate_optimization_layout(output_dir, base_positions)` 会读取
`baseline-design.json` 和每个候选的可逆 patch，恢复候选网表，逐个运行同一组有界
布局搜索，并写出 `layout-plan.json`、`layout-results.json` 和
`combined-ranking.json`。最终候选必须同时满足原参数实验的电气状态和本轮布局几何
门禁；缺少 patch、网表或布局证据的候选会记录为失败而不会被提升。默认 evaluator
使用真实的 `validate_layout_positions`，也可注入一个带有
`(netlist, positions, evaluation_id)` 签名的确定性 evaluator 做离线回归。

传入 `materialize=true` 可为每个选中的布局调用材料化器，默认复用
`materialize_layout_positions` 并把 `.ms14` 放入优化目录的 `layouts/` 子目录；这一步
只证明构建器布局和编码成功，返回的 `native_acceptance` 仍保持 `pending`，必须继续做
Multisim 保存重开、ReportNetlist、参数回读和仿真验收。商业版的跨板联合搜索、全局总线
布线和制造约束不由这个入口扩展。

`optimize_single_board_layout` 不修改 `.ms14`、不改变网络和引脚，也不启动 Multisim。
调用方应把 `validate_layout_positions` 或等价的构建/几何门禁作为 evaluator，并在候选
通过后继续执行保存重开、ReportNetlist、仿真和报告验收。没有可行候选时返回
`no-feasible-layout`，不会把失败布局标成优化成功。

MCP 仍复用现有 `validate_layout_positions` 工具；传入 `optimize=true`、`positions`、
`probe_nets`、`step`、`moves` 和 `max_candidates` 即可启用这层有界搜索，默认行为和旧接口完全一致。
`positions` 同时接受优化输入常用的 `{"R1": [x, y]}` 和上一次验证直接返回的
`{"R1": {"x": x, "y": y}}` 两种形式，便于把验证结果直接送入下一轮优化。
地符号 `0`/`GND` 默认视为固定锚点，不参与局部移动。

需要生成新工程时，可在同一次调用中显式设置 `materialize=true` 和
`output_ms14`。该路径按原始网表、选中坐标和 `probe_nets` 重新生成工程，避免对旧工程
原地改线造成探针丢失；生成结果仍带有 `native_acceptance=pending`，必须继续执行原生验收。

建议的调用顺序如下：

1. 先调用 `validate_layout_positions(netlist, positions)`，得到基线的
   `placements`、交叉率、导线长度和弯折数；
2. 将该 `placements` 原样传回 `validate_layout_positions`，并设置
   `optimize=true`，限制 `max_candidates`；
3. 只有当返回 `status=pass` 且 `selected` 不为空时，才把
   `selected.positions` 交给现有的 `set_component_positions`；
4. 对生成的 `.ms14` 执行保存重开、拓扑回读、参数回读、原生仿真和报告导出。

代码调用方也可以使用 `component_placement.apply_optimized_layout_candidate`，
它会复用同一个位置写入器，并对写入后的坐标做一次解码回读；回读不一致时返回
`state=readback-mismatch`，出现回退导线或元件重叠时返回
`state=rejected-geometry`，不会把文件标记为可交付。

本机 Multisim 14.3 的原生结果和失败关闭证据见
[`SINGLE_BOARD_LAYOUT_NATIVE_ACCEPTANCE_20261018.md`](SINGLE_BOARD_LAYOUT_NATIVE_ACCEPTANCE_20261018.md)。

优化器本身只证明候选在构建器几何门禁下可行，不替代第 4 步的 Multisim 验收。
如果第 4 步失败，应保留失败证据并回退到上一个已验收版本。

该层只覆盖单板的有界局部移动和可读性评分。跨板联合布局、跨板参数/拓扑联合搜索、
全局高级排线、总线/扇出优化和制造约束属于商业版范围。

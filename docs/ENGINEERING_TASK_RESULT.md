# 自然语言工程任务返回协议

RC、RLC 和 OPAMP 三个现有运行器均通过 `normalize_task_result` 返回预览，通过 `finalize_task_result` 保存执行结果。MCP 入口沿用同一返回值。

| 字段 | 含义 |
| --- | --- |
| mode | preview 或 execute |
| stage | 预览为 preview；执行结束为 complete 或 failed |
| last_stage | 有阶段记录时，保留结束前的阶段，用于定位失败 |
| task_id | 直接同步调用为 null；本协议不自行创建队列任务 |
| native_project | 已存在的原生工程路径；没有对应证据时为 null |
| topology_acceptance | 原生拓扑核验数据；尚未取得时为 null |
| measurement_acceptance | 结构化验收数据；不能用文件路径代替 |
| optimization | RC/RLC 的候选数量与选择结果；未实现搜索的入口为 null |
| artifacts | 相对 output_dir 的已落盘证据路径，包含验收记录和 manifest |
| error | 异常信息或 null；是否通过以 success、verification_status 为准 |

保留旧字段以兼容已有调用者。RC/RLC 的 `acceptance` 仍是文件路径，OPAMP 的旧 `acceptance` 仍是对象；新调用者应读取 `measurement_acceptance`。

执行结束时，返回值与 `acceptance.json` 一致。报告先生成，再保存最终验收记录，最后生成哈希清单，避免修改验收文件后留下过期哈希。清单不包含自己的哈希。

验证范围：本次验证覆盖预览无副作用、注入生成失败、成功终态归一化、落盘一致性、清单哈希以及相关 MCP 回归。没有据此宣称完整 OPAMP 原生运行已通过，也没有新增 COM 硬超时或断点恢复。

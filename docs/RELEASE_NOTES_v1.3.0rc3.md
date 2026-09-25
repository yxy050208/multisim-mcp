# Multisim MCP 1.3.0rc3（源码候选版）

`1.3.0rc3` 是当前源码树的开发候选版，尚未创建 GitHub/PyPI 公开发行包。
公开稳定版仍为 `v1.2.0`；已发布的 `v1.3.0rc1` 说明保留在
[`RELEASE_NOTES_v1.3.0rc1.md`](RELEASE_NOTES_v1.3.0rc1.md)。

## 本次整合

- 合并 PR #27，保留社区贡献者 `ijay11111` 的原始提交作者信息，并将 PR #26
  的布局能力与主分支兼容性修复统一到 `main`。
- 新增 `component_placement.py` 及 `list_component_positions`、
  `set_component_positions`、`check_component_overlap`、
  `validate_layout_positions`、`set_sheet_size` 工具。
- `create_schematic_from_netlist` 支持固定元件位置、图纸尺寸和 `verify=False`
  快速构建；正交布线器加入空间分桶，减少大图纸构建时间。
- 恢复 LED 流水灯、呼吸灯、SWB 端子顺序、`.model/.subckt` 分行、COM `gen_py`
  缓存自愈、多节元件 `U1 -> U1A` 拓扑别名、模板包完整性检查和位号映射。
- 统一 MCP profile 与 stdio 契约：`core=34`、`experiment=89`、
  `optimization=70`、`full=112`。

## 验证边界

- 主分支最新提交：`97d4ac2`。
- 公开 CI 的 `checks`、`windows-x86-protocol`、`linux-introspection` 和
  `linux-ngspice` 全部通过；本地 Harness 契约检查通过。
- 当前完整测试基线为 `796 passed, 45 skipped, 139 subtests`（共 841 个测试项）；真实 Multisim 证据集中在已记录的
  14.3 环境和组件族，不能据此宣称任意复杂电路或生产级认证。
- 已加入数字信号链布局和 `crossings_per_wire` 几何错误；仍需对数字电路、LED
  板和多板工程建立真实 Multisim 回归基准，并在交付层把失败结果阻断或自动修复。

## 安装边界

源码候选版要求本机安装并授权 Multisim 14+，由独立 32 位 Python worker 执行
COM；MCP 前端可以使用 32 或 64 位 Python。公开 npm Harness bundle 目前仍是
`1.3.0-rc.1`，直到单独发布新 bundle 前不要把它写成 rc3。

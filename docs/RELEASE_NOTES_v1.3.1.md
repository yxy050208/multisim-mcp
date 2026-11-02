# Multisim MCP 1.3.1

这是基于 `v1.3.0` 的修复版本，重点修复 Multisim 图形界面拖动元件后出现悬空节点的问题。

## 修复

- 多分支网络的 junction owner 现在同时写入图纸 `Objects` 与 `ReferencedComponents`。
- 新增回归测试，确认多个 junction owner 在生成 XML 中均有引用。
- 修复版本与 PyPI、官方 MCP Registry、DeepSeek Harness npm bundle 的版本号保持一致。

## 验证

- PR #41 已合并到 `release/v1.3.0`，合并提交：`b53b40a4850f772cdebf651e18de048eecbadbb5`。
- PR CI 的 Linux、Windows 协议和检查任务全部通过。
- 本地定向回归测试 10 项通过。
- `v1.3.0` 已存在于 PyPI 和 MCP Registry，不能覆盖，因此本修复使用 `v1.3.1` 独立发布。

## English

This is a repair release based on `v1.3.0`. It fixes the Multisim GUI floating-junction issue that appeared after dragging components in generated multi-drop networks.

The junction owner is now emitted in both the diagram `Objects` collection and `ReferencedComponents`. A regression test covers multiple junction owners. The repair is versioned as `1.3.1` because PyPI and the MCP Registry already contain immutable `1.3.0` artifacts.

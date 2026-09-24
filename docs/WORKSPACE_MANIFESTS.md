# 版本化工作目录 Manifest / Versioned Workspace Manifests

Multisim MCP 使用根目录下固定名称 `directory.manifest.json` 描述可长期恢复和审计的
项目、实验、优化与设计比较目录。该文件属于传输无关的 EDA Core：MCP、本地 API、未来的可视化
工作台和不同 EDA 后端共享同一格式。

## 适用目录

- `project`：电路设计、模型引用、补丁和项目级附件；
- `experiment`：一次原理图生成、仿真、验收和报告事务；
- `optimization`：参数扫描、候选比较及后续的有界优化运行。
- `global-optimization`：参数/拓扑混合搜索、Pareto 前沿和候选级恢复证据。
- `autonomous-correction`：模型规划的多轮纠错、严格改进选择和轮次级恢复证据。
- `benchmark-suite`：跨电路族基准摘要及其嵌套实验/优化产物。
- `comparison`：多个完整电路设计或拓扑的统一验收与确定性排名。

当前完整实验和参数扫描会在事务发布前自动生成 manifest。项目目录和未来优化器可直接
调用 `write_directory_manifest` 使用同一契约。

受控审批实验还可在 `metadata.approval_provenance` 写入
`multisim-mcp-approved-simulation-provenance` 摘要。它只包含 simulation-plan、网表、
compiled、设计和 ExperimentSpec 的稳定 ID/SHA-256；完整规格、审阅备注、输出路径和
审批令牌不会进入 manifest。工作台回读结果时会同时校验路径、manifest 完整性和该摘要，
因此旧实验或未带归属摘要的直接执行结果不会被自动标为当前审批结果。

## Schema 1

```json
{
  "schema_version": 1,
  "manifest_type": "multisim-mcp-directory",
  "manifest_id": "manifest-0123456789abcdef01234567",
  "directory_kind": "experiment",
  "entity_id": "exp-0123456789abcdef01234567",
  "state": "succeeded",
  "revision": 0,
  "created_at": "2026-08-21T00:00:00Z",
  "updated_at": "2026-08-21T00:00:00Z",
  "producer": {"name": "multisim-mcp", "version": "1.1.0"},
  "artifacts": [
    {
      "path": "result.raw",
      "role": "simulation-data",
      "size": 1234,
      "sha256": "<64 lowercase hexadecimal characters>"
    }
  ],
  "metadata": {"verified": true}
}
```

所有产物路径必须是使用 `/` 的相对路径，不能包含 `..`、绝对路径或符号链接。
manifest 不散列自身，以避免递归摘要。重复写入同一目录时保留 `created_at`，并递增
`revision`。

## 完整性与失败行为

`read_directory_manifest(..., verify=True)` 默认逐项核对文件大小和 SHA-256。以下情况
会失败关闭：

- schema 版本、类型、目录种类或字段未知；
- 产物缺失、被替换、大小变化或摘要不匹配；
- 路径越界、符号链接、重复路径或非有限 JSON 数值；
- manifest 超过大小限制或产物条目超过硬上限。

写入使用同目录临时文件、`fsync` 和原子替换。Windows 短暂共享冲突只进行有界退避
重试，永久权限错误仍会返回给调用方。

## 与 `manifest.json` 的区别

实验目录中的 `manifest.json` 是正式报告使用的可复现产物清单；
`directory.manifest.json` 是项目生命周期、状态、修订和目录恢复契约。后者会散列前者，
而前者不反向散列目录 manifest，避免循环依赖。

## English summary

`directory.manifest.json` is the strict, versioned persistence contract shared
by project, experiment, and optimization folders. It records lifecycle state,
revision, producer version, portable relative artifact paths, sizes, and
SHA-256 digests. Reads verify integrity by default and reject unknown schemas,
path traversal, symlinks, missing files, and tampering. Complete experiments
and sweeps create the manifest inside their existing atomic publication
transactions.

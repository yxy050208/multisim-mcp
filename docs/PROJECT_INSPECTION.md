# 只读项目审查快照 / Read-only Project Inspection

## 用途

阶段 D 的第一步不是立即重造 EDA 编辑器，而是先提供一个稳定、只读、可供
前端复用的项目审查数据层。`inspect_project` 只读取版本化的
`directory.manifest.json`，汇总项目、实验、优化、全局优化、自动纠错和比较
目录的状态、产物角色和完整性结果。

它不会：

- 修改工程、重写 manifest 或运行仿真；
- 把没有 manifest 的任意目录猜测成实验；
- 跟随符号链接；
- 因一个损坏的子目录而丢弃整个项目快照。

损坏的子 manifest 会作为 `integrity_status: "invalid"` 条目返回，快照整体的
`success` 也会变为 `false`，便于 GUI 用醒目标记提醒人工检查。

## CLI

```powershell
multisim-mcp inspect-project --root . --json
multisim-mcp inspect-project --root . --max-entries 128 --max-depth 4
multisim-mcp inspect-project --root . --no-verify --json
```

### 本机工作台 API

工作台可以通过一个额外的、只绑定回环地址的 HTTP 桥接读取同一份快照：

```powershell
python -m multisim_mcp.cli workbench-api `
  --root C:\path\to\project `
  --host 127.0.0.1 `
  --port 8787
```

桥接提供 `GET /api/health`、`GET /api/project-snapshot`、实验/优化条目详情以及固定的
`plot` / `schematic` 实验媒体读取。工程根目录在启动时固定，浏览器不能通过请求参数
切换路径；条目使用由服务生成的 opaque handle，不把相对路径当作 API 参数。详情
复用现有实验 Resource 汇总逻辑，报告最多返回有界预览，媒体必须存在于已验证的
directory manifest 中。SVG 仅作为带 sandbox CSP 的图片返回，不注入页面 DOM。

`optimization` 与 `global-optimization` 详情复用优化器已经持久化的状态、规格和
Pareto 文件。响应包含预算/停止原因、状态计数、最多 512 个候选摘要、单目标收敛或
多目标点集、Pareto 层级、推荐方案和基于候选取值跨度的敏感度摘要，但不返回补丁路径、
实验目录或原始错误路径。敏感度方法标记为 `observed-candidate-range`，只能解释已测
候选中的相对影响，不能替代局部导数、全局灵敏度分析或因果结论。详情还会附带一个
`search_plan`：按影响度给出有界的 E24 数值邻域或已观测离散值，作为人工复核用的下一轮
搜索建议；它还带有不可执行的 `spec_draft` 和提交预检，可复制/下载后由用户审阅并另行
提交。预检只检查有界预算和值域，并标记必须人工批准；草案明确标记为只读，不会启动实验、
签发审批令牌、写入规格或改变优化状态。预检中的 `approval` 状态固定为
`not_issued`，令牌只能通过独立的本机 `search-plan-approve` CLI 签发，并精确绑定
草案 SHA-256、优化条目和预算摘要；`search-plan-verify` 只校验、不消费令牌。
JSON 在 manifest 校验后再次核对大小与 SHA-256，避免校验和读取之间被替换。

扫描深度、条目数和 `--no-verify` 与 CLI 一致。所有端点均不写文件、不启动仿真。
不要把该服务绑定到公网地址；它不是远程多租户 API。

默认逐项校验 manifest 引用的文件大小和 SHA-256。`--no-verify` 只适合快速浏览，
不会把未校验内容报告为完整性通过。

机器可读结果的核心字段：

- `root_manifest`：项目根 manifest（若存在）；
- `entries`：每个发现的 manifest、状态、产物预览和完整性状态；
- `summary`：按目录种类、生命周期状态和完整性分类的计数；
- `limits`：扫描深度、条目上限、是否截断以及是否启用校验。

条目中的产物预览最多 256 项；扫描最多 2,048 个 manifest、8 层目录，避免把
大型工程目录一次性加载进 MCP/GUI 上下文。

## 后续工作台接入

只读工作台通过 loopback-only 的 `workbench-api` 读取这份有界快照，后续再通过已有
的实验 Resource 读取波形、原理图、报告和 SPICE 兼容性档案。快照只负责工程导航和
完整性状态，不复制仿真或优化业务逻辑。

## English summary

`inspect_project` is the first Stage-D building block. It returns a bounded,
read-only snapshot of manifest-backed project directories for a future GUI or
local API; the optional `workbench-api` bridge serves the same snapshot,
manifest-backed experiment details/media, and bounded optimization/Pareto
evidence only on loopback. It verifies hashes
by default, never follows symlinks, does not infer
unmanifested directories, and reports corrupt child manifests without hiding
the rest of the project. The CLI is `multisim-mcp inspect-project --root .
--json`; it does not add or change MCP tool profiles.

# Search-plan approval / 搜索草案审批

`search_plan.spec_draft` is a read-only review payload. It is deliberately not
an `OptimizationSpec` and cannot start a simulation. The Workbench only shows
the preflight and reports `approval.status = not_issued`; it never creates or
consumes a token. Submission remains a local CLI boundary so a browser, model,
or MCP caller cannot turn copy/download into execution.

## Issue an approval / 签发审批

After reviewing the draft, an operator may issue a short-lived, one-time
approval with the local CLI:

```text
python -m multisim_mcp.cli search-plan-approve \
  --spec-draft search-draft.json \
  --source-design design.json \
  --source-spec optimization-spec.json \
  --entry-handle entry-global-... \
  --optimization-id global-... \
  --optimization-kind global-optimization \
  --exploration-budget 7 \
  --max-experiments 8 \
  --approval-store .state/search-approvals \
  --token-output .state/search.token
```

The command stores only a SHA-256 token digest in the approval record and
writes the raw bearer token once to a create-only, user-only token file. It
does not submit a task or modify an optimization directory.

The approval is bound exactly to:

- opaque entry handle and optimization ID;
- source optimization kind;
- SHA-256 of the normalized source `CircuitDesign` JSON;
- SHA-256 of the complete source optimization specification JSON;
- SHA-256 of the complete canonical `spec_draft` JSON;
- exploration budget and maximum experiment count.

Changing any of these values, including a single source or draft value, makes
verification fail. TTL is 60–86,400 seconds (15 minutes by default). The
`--source-design` and `--source-spec` arguments are required for approval and
verification; the same exact inputs must be supplied at submission time.
The approval-record schema is now v2 because source-design and source-spec
digests are mandatory; records issued by the earlier v1 boundary must be
reviewed and reissued rather than silently migrated.

## Submit an approved plan / 提交已批准草案

After the operator has reviewed the token with `search-plan-verify`, consume it
and enqueue one derived, bounded formal optimization:

```text
python -m multisim_mcp.cli search-plan-submit \
  --spec-draft search-draft.json \
  --source-design design.json \
  --source-spec optimization-spec.json \
  --entry-handle entry-global-... \
  --optimization-id global-... \
  --optimization-kind global-optimization \
  --exploration-budget 7 \
  --max-experiments 8 \
  --approval-store .state/search-approvals \
  --approval-token-file .state/search.token \
  --job-dir .state/jobs \
  --output runs/search-001 \
  --json
```

The command revalidates the design, source specification, draft, budget and
runtime limits before claiming the token. It converts scalar draft values into
the formal optimization specification, persists a durable `optimization` or
`global_optimization` job, then consumes the token. The bearer token is never
written into the job record; only the approval ID and binding/spec digests are
retained. If the process crashes after queue persistence but before token
consumption, the approval ID makes a retry idempotently return the same job
instead of creating a duplicate.

`search-plan-submit` is a queue hand-off and reports `execution_started=false`.
Use the same `MULTISIM_MCP_JOB_DIR` (or `--job-dir`) when starting the long-lived
MCP server so its isolated worker drains the queue and publishes normal job
status/resources. A topology-choice proposal cannot be converted from scalar
values; it must be submitted through an explicit topology operation workflow.
The token is one-time and a second claim is rejected. The
`search-plan-verify` command remains non-consuming and exists for pre-submit
checks only.

## 安全边界

- 工作台仍然只读；复制/下载草案不等于批准。
- 签发令牌不会执行实验，也不会写入 `OptimizationSpec`。
- 令牌不打印、不进入 MCP 响应；日志和记录不保存明文 bearer secret。
- 工作台仍不会启动提交；实际提交只能走本机 `search-plan-submit` CLI，且必须带上
  与审批时相同的设计、来源规格和草案。
- `search-plan-submit` 只创建 durable queue 记录，`execution_started` 仍为 `false`；
  由同一 `MULTISIM_MCP_JOB_DIR` 的长驻 MCP worker 负责后续实验。
- 提交前会重新校验规范；拓扑选择不能从标量草案自动推导，避免把不明确的结构变化
  伪装成元件值搜索。

# DeepSeek Harness 插件 npm 发布手册

本文只适用于 `integrations/deepseek-harness` 中的独立 npm bundle。Python
包、MCP Registry 和该 bundle 是三个独立发布物；版本当前保持同步，但不能由同一个
上传步骤隐式联动。

> 发布状态：`multisim-mcp-dsh-plugin@1.1.0` 已于 2026-08-23 使用维护者 2FA
> 首次公开发布。Registry integrity 为
> `sha512-tdZ2d3tw5lxe7Dg5aCTEZekw8tIb0jqk3d8j8o39BylKrEs+aPOiuXka1mNT4pYVLLdkzAZ6gEiRUHzf0pMEqQ==`。
> 发布后已从 npm 包名完成隔离安装、Cordis 合成、MCP 启动和 Web HTTP 200 验证。

## 安全边界

- 包名固定为 `multisim-mcp-dsh-plugin`，发布前必须再次查询 Registry；
- npm 包只能包含 `package.json`、`README.md`、`LICENSE` 和
  `cordis.patch.yml`；
- 仓库工作流不保存 `NPM_TOKEN`，后续版本使用 GitHub OIDC；
- npm 的 `DEEPSEEK_API_KEY`、Multisim 文件、实验产物和本地绝对路径都不得进入包；
- GitHub `npm` Environment 应配置 required reviewer，防止误触发上传。

本地预检：

```powershell
python tools/check_dsh_plugin_release.py `
  --expected-version 1.1.0 `
  --check-registry `
  --json
npm pack .\integrations\deepseek-harness --dry-run --json --ignore-scripts
```

发布守卫把 Registry 的 404 解释为“当前未被占用”，但这不是名称保留。首次发布前
必须重新执行；如果 Registry 已出现同名包且其 repository 不是本项目，守卫会失败。

## 首次发布 1.1.0

npm 的 staged publishing 明确要求包已经存在，因此全新包不能使用
`npm stage publish`。首次发布需要包所有者在可信本机完成，并使用账户 2FA：

```powershell
cd integrations\deepseek-harness
npm login
npm pack --json --ignore-scripts
npm publish .\multisim-mcp-dsh-plugin-1.1.0.tgz --access public
```

不要把一次性验证码写入脚本、文档或 shell 历史；让 npm 在交互提示中请求 2FA。
发布前应人工查看 `npm pack --dry-run --json` 的文件列表和 tarball SHA-256。发布后
立即核对：

```powershell
npm view multisim-mcp-dsh-plugin@1.1.0 `
  name version repository dist.integrity dist.shasum --json
```

## 首次发布后的 Trusted Publishing

在 npmjs.com 的该包 Settings → Trusted Publisher 中配置：

| 字段 | 值 |
| --- | --- |
| Provider | GitHub Actions |
| Organization or user | `yxy050208` |
| Repository | `multisim-mcp` |
| Workflow filename | `publish-dsh-plugin.yml` |
| Environment | `npm` |
| Allowed actions | 只允许 `npm stage publish` |

然后把 package publishing access 设置为“Require two-factor authentication and
disallow tokens”，并撤销不再需要的自动化 Token。每个 npm 包只能配置一个 Trusted
Publisher；工作流文件名和 Environment 名必须完全一致。

## 后续版本

1. 同步 Python 包、兼容性清单与 bundle 的版本，并通过 CI。
2. 手动运行 GitHub Actions 的 `Verify or stage DeepSeek Harness plugin`。
3. 输入目标版本，选择 `stage-existing`，确认文本填写
   `stage multisim-mcp-dsh-plugin@<version>`。
4. `npm` Environment reviewer 批准后，工作流使用短期 OIDC 凭据上传暂存包。
5. 在 npmjs.com 的 Staged Packages 页面下载并复核 tarball，再以账户 2FA 批准。
6. 用 `npm view` 核对版本、repository、integrity 和 provenance。

默认的 `verify` 操作只验证版本、Registry 归属和打包边界，不请求 OIDC，也不会上传。
暂存工作流会拒绝尚不存在的包、已经发布的版本以及 repository 不属于本项目的同名包。

## English summary

Version `1.1.0` was published interactively with maintainer 2FA on 2026-08-23
and verified through a clean Registry install and live Harness startup. Configure
`publish-dsh-plugin.yml` as a stage-only trusted publisher bound to the `npm`
GitHub Environment. Future CI runs use short-lived OIDC credentials to stage,
while a maintainer still reviews and approves every release with 2FA. No
long-lived npm publish token is stored in the repository.

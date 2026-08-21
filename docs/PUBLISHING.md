# 公开发布指南 / Public Publishing Guide

本文以中文为主，英文摘要位于每节末尾。目标是发布项目自有代码，同时避免误传
NI 样例、解码设计、许可材料、个人路径和来源未确认的 XML 模板。

## 1. 发布内容边界

公开仓库可以包含：

- MCP server、COM 适配层、SPICE 解析和报告代码；
- 单元测试、CI、MIT License、安全策略和贡献指南；
- 模板提取器、来源 manifest 和用户本地模板包生成器；
- 不含第三方设计内容的文字文档。

公开仓库默认不包含：

- `analysis/` 下的研究文件和实验输出；
- `.ms14`、解码 `.ms14.xml`、类型库转储、NI 示例电路；
- `mcp_server/multisim_mcp/templates/*.xml`；
- wheel、嵌入式 Python、npm 缓存、截图和包含个人绝对路径的结果。

English: publish the engine, tests, docs, manifest, and local extractor. Exclude
installed samples, decoded files, extracted XML packs, binaries, and local output.

## 2. 发布账户与仓库检查

项目 URL 和 `origin` 已配置为公开仓库。每次发布前仍需确认：

- `git remote get-url origin` 指向 `yxy050208/multisim-mcp`；
- PyPI Trusted Publishing environment 仍为 `pypi`；
- GitHub Private vulnerability reporting 保持开启；
- `pyproject.toml`、`server.json` 和 `multisim_mcp.__version__` 三处版本一致。

English: verify the configured repository, trusted publisher, security settings,
and synchronized package/registry versions before every release.

## 3. 本地审计

不要使用未经检查的 `git add .`。先运行：

```powershell
python tools/release_audit.py
git status --short
git diff --check
```

审计必须为 PASS。若报告已跟踪的研究文件，应使用精确路径从 Git 索引移除，保留
本地副本；不要用会删除工作区内容的命令。

English: run the audit before staging and add reviewed paths explicitly.

## 4. 验证命令

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
C:\path\to\python32\python.exe -m unittest discover `
  -s .\mcp_server\tests -p 'test_*.py'
python -m build .\mcp_server
```

真实 Multisim 回归需要 32 位 Python、本机授权的 Multisim 和本地模板包，不放入
公共 CI。CI 只运行无 COM 测试和代码/资源审计。

English: public CI remains COM-free; run the real Multisim suite locally.

## 5. 发布提交和标签

```powershell
git add <逐项审查过的路径>
git diff --cached --check
python tools/release_audit.py
git commit -m "release: v1.1.0"
git tag -a v1.1.0 -m "Multisim MCP v1.1.0"
git show --no-patch --decorate v1.1.0
```

签注标签只用来锁定已审查源码，**推送标签本身不会触发 PyPI 发布**。
`publish-pypi.yml` 只支持手动 `workflow_dispatch`；推送标签后，必须在该
标签引用上手动运行工作流，并核对运行的 head SHA 与标签解引用提交一致。
创建 GitHub Release 和上传附件也需要单独手动执行。不要上传包含本地
XML 模板的开发 wheel。

English: an annotated tag pins reviewed source but does not publish anything.
Manually dispatch `publish-pypi.yml` at that tag and verify the run SHA; never
attach the local template wheel.

## 6. v1.1.0 发布顺序

1. 推送发布提交并等待 `CI` 全部通过。
2. 创建并推送签注标签 `v1.1.0`，确认远端标签解引用到已通过 CI 的
   `main` 合并提交。此操作不会自动发布 PyPI。
3. 在标签引用上手动运行 Trusted Publishing，并等待成功：

   ```powershell
   gh workflow run publish-pypi.yml --repo yxy050208/multisim-mcp --ref v1.1.0
   ```

4. 核对 PyPI JSON API 中的版本、文件名和 SHA-256；再使用双语发布说明创建
   GitHub Release。如果上传 wheel/sdist，只附加从 PyPI 下载并验证过的同一
   份发布物，不使用本地模板开发包。
5. 在同一标签引用上手动运行 MCP Registry 发布工作流，并确认
   `io.github.yxy050208/multisim-mcp` 显示
   `1.1.0`。

   ```powershell
   gh workflow run publish-mcp-registry.yml `
     --repo yxy050208/multisim-mcp --ref v1.1.0
   ```

6. 核对 Glama、awesome-mcp-servers 等社区目录的仓库链接和徽章；目录更新不能先于
   可安装包和 Registry 元数据。

English: publish in order—green CI, annotated tag, manual PyPI dispatch at the
tag, verified GitHub Release, manual MCP Registry dispatch, then community
directory metadata.

## 7. DeepSeek Harness npm bundle

`integrations/deepseek-harness` 是独立 npm 发布物。首次发布不能使用 npm staged
publishing，必须由维护者本地登录并通过 2FA 发布。包存在后，在 npm 中把
`publish-dsh-plugin.yml` 配置为绑定 GitHub `npm` Environment、且只允许
`npm stage publish` 的 Trusted Publisher；后续版本先由 OIDC 暂存，再由维护者以
2FA 审批。仓库不保存长期 npm 发布 Token。

完整预检、首次发布和后续审批步骤见
[`DEEPSEEK_HARNESS_NPM_RELEASE.md`](DEEPSEEK_HARNESS_NPM_RELEASE.md)。

English: the first npm publication is interactive with 2FA. Later releases use
stage-only OIDC and require a separate maintainer review and 2FA approval.

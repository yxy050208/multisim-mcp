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

## 2. 首次发布前必须填写

当前没有 Git remote，因此发布者需要在 `mcp_server/pyproject.toml` 增加真实 URL：

```toml
[project.urls]
Homepage = "https://github.com/<OWNER>/<REPOSITORY>"
Repository = "https://github.com/<OWNER>/<REPOSITORY>"
Issues = "https://github.com/<OWNER>/<REPOSITORY>/issues"
```

同时把 `SECURITY.md` 的临时报告说明替换为 GitHub Security Advisory 地址，并在
仓库 Settings → Security 中启用 Private vulnerability reporting。

English: set real project URLs and enable GitHub private vulnerability reporting.

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

## 5. 建议的首次提交和标签

```powershell
git add <逐项审查过的路径>
git diff --cached --check
python tools/release_audit.py
git commit -m "release: v0.1.0-alpha"
git tag -a v0.1.0-alpha -m "Multisim MCP v0.1.0-alpha"
```

推送、创建 GitHub Release 和上传附件应在再次检查暂存内容后手动执行。不要上传当前
包含本地 XML 模板的开发 wheel。

English: tag only after a final staged-file audit; never attach the local template wheel.

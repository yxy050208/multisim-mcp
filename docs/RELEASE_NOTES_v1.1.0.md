# Multisim MCP v1.1.0

这是从“Multisim 自动化工具集合”走向可扩展 EDA 自动化平台内核的首个版本。
它保留 v1.0 的电路生成、真实仿真、数据导出和双语实验报告闭环，同时加入稳定的
EDA 数据边界、可注入后端、模型运行时、DeepSeek/DeepSeek Harness 集成，以及可验证
的项目、实验和优化目录契约。

## 主要变化

- 新增传输无关的 `CircuitDesign`、`DesignPatch`、`ArtifactSet` 和
  `ExperimentRequest`，让电路设计、补丁、产物及实验请求不再依赖 MCP 或 COM。
- 新增 `EdaBackend` 能力发现、注册和调度服务；Multisim 是第一个适配后端，后续可在
  不破坏上层接口的前提下扩展其他 EDA 平台。
- `create_schematic_from_netlist`、`run_spice_netlist` 和持久实验任务逐步归一到同一应用
  服务边界，32 位 COM worker 与 32/64 位 MCP 前端继续隔离运行。
- 新增 DeepSeek、OpenAI、Ollama 与兼容端点的自助配置和有界模型运行时；密钥只以
  环境变量引用保存，输出脱敏，远程明文 HTTP 和未授权工具调用会被拒绝。
- 新增只读 `model-diagnose` 工具循环，可对严格 `CircuitDesign` 或仅解析的安全 SPICE
  网表执行摘要、元件分页、网络连通性和结构检查；不会触发 Multisim 或修改工程。
- 增加 DeepSeek Harness 的 profiles、工具描述、skills 和 npm bundle 源码。该 bundle
  是独立发布物，源码版本与 Python/MCP 同步为 `1.1.0`，但不会随
  Python 包自动发布；npm 仍需维护者单独完成 2FA 和 Registry 守卫。
- 将真实课程设计回归中暴露的频率、幅度和 THD 测量问题纳入实现，并扩展受控厂商
  subcircuit 和双 LM324 等复杂模拟电路路径。
- 新增严格 `directory.manifest.json`：项目、完整实验和参数扫描/优化目录共享 schema、
  生命周期状态、修订号、生成器版本以及每项产物的大小和 SHA-256。读取时默认拒绝路径
  越界、符号链接、未知字段和内容篡改。
- 修复 Windows 下持久任务状态文件原子替换偶发共享冲突的问题，并以有限重试保持
  失败关闭语义。

## 安装

真实 Multisim 自动化要求 Windows、已授权的 Multisim 14+，以及可供 COM worker
使用的 32 位 Python 3.10+：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==1.1.0"
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang zh --connect
```

MCP 前端也可运行在 64 位 Python，并通过 `MULTISIM_MCP_WORKER_PYTHON` 指向安装了本
项目与 `pywin32` 的 32 位解释器。Linux/Docker 仍仅支持协议 introspection、配置和
兼容性检查，不能运行 Multisim COM 自动化。

公开包不包含 NI 软件、样例、解码电路或从本地安装提取的 XML 模板。元件模板包必须
由用户从自己已授权的 Multisim 安装中本地生成。

## 协议与验证

- 保持 55 个 MCP tools、19 个 Resource templates 和 5 个中英双语 prompts。
- Python 要求保持 `>=3.10`；32 位 Python 负责真实 COM，64 位前端通过持久 worker
  调用它。
- 发布预检已在 32 位 Python 3.12 上通过 271 项无 COM 测试，并在 64 位 Python
  3.10 上通过目录清单、实验管线和任务引擎核心回归。
- `directory.manifest.json` 与正式实验 `manifest.json` 职责不同：前者约束目录级
  生命周期和全部产物完整性，后者继续保存实验复现语义。
- 公开 CI 与最终 wheel/sdist 内容审计将在发布提交后再次执行；真实 Multisim 回归仍
  必须在安装并授权 NI 软件的本地 Windows 主机上完成。

## 升级说明

v1.0 客户端配置和 MCP 工具调用保持兼容。新目录清单由新实验和参数扫描自动生成；
旧输出目录不会被静默改写。如需验证新目录，可读取
[`WORKSPACE_MANIFESTS.md`](WORKSPACE_MANIFESTS.md) 中的格式和安全约束。

## English summary

Version 1.1 evolves Multisim MCP into an extensible EDA automation core while
preserving the v1.0 circuit-generation, real-simulation, export, and bilingual
report workflow. It introduces transport-independent design and experiment
models, injectable EDA backends, bounded DeepSeek/OpenAI/Ollama integration,
read-only model-assisted diagnosis, and strict versioned directory manifests
for projects, experiments, and optimization runs. The public protocol remains
compatible at 55 tools, 19 Resource templates, and 5 prompts. Real Multisim
automation still requires licensed NI software on Windows and a 32-bit Python
COM worker.

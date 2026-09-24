# Multisim MCP 1.3.0rc1

这是可安装的工程工作流候选版，整合原开发分支和 PR #13 的中文路径修复。它是预发布版本，保留稳定版 1.2.0。

- 自然语言 RC/RLC、受限运放、DC 分压和 2N3904 共射入口，以及组合模拟电路工作流。
- Multisim 原生图纸、OP/AC/TRAN、模型/引脚/网表检查、测量指标和实验报告导出。
- 共射图纸采用原生 DC/PULSE 源、工程单位显示和按信号流向组织的布局。
- 修复 Windows 非 ASCII 路径在 worker 标准输入中乱码的问题，ping 增加编码诊断（感谢 PR #13 贡献者）。
- 安装包包含版本化组件映射；本地 NI XML 模板仍须从用户授权安装中提取。
- CI 的无 COM 测试隔离版本检测，Docker 协议检查同步到 104 个工具。

安装：`python -m pip install multisim-mcp==1.3.0rc1`。64 位 MCP 前端仍需配置独立的 32 位 Multisim COM worker。

边界：真实元件实测集中于 Multisim 14.3、LM324AJ 和 2N3904；新图纸仍需人工复核。尚不保证任意复杂电路、容差/温漂/噪声分析、开关电源或多板自动生成。此版本不包含独立桌面前端。

## English

This installable release candidate combines the engineering development branch with PR #13's UTF-8 worker fix. Stable 1.2.0 remains available.

It adds bounded natural-language and composed analog workflows, native Multisim projects, OP/AC/TRAN evidence, topology/model checks and report exports. Component compatibility manifests now ship in the wheel. Licensed NI templates and local experiment outputs are excluded.

Install `multisim-mcp==1.3.0rc1`. Native simulation requires licensed Windows Multisim and a 32-bit COM worker. Real-device evidence is limited to the documented Multisim 14.3 samples; new schematics require visual review. This is not an arbitrary-circuit or production-wide engineering certification.

The independently versioned npm adapter is `multisim-mcp-dsh-plugin@1.3.0-rc.1` and uses the separately installed Python core.

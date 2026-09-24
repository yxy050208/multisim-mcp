# Multisim MCP 1.3.0rc1

这是可安装的工程工作流候选版，整合原开发分支和 PR #13 的中文路径修复。它是预发布版本，保留稳定版 1.2.0。

- 自然语言 RC/RLC、受限运放、DC 分压和 2N3904 共射入口，以及组合模拟电路工作流。
- Multisim 原生图纸、OP/AC/TRAN、模型/引脚/网表检查、测量指标和实验报告导出。
- 共射图纸采用原生 DC/PULSE 源、工程单位显示和按信号流向组织的布局。
- 修复 Windows 非 ASCII 路径在 worker 标准输入中乱码的问题，ping 增加编码诊断（感谢 PR #13 贡献者）。
- 安装包包含版本化组件映射；本地 NI XML 模板仍须从用户授权安装中提取。
- CI 的无 COM 测试隔离版本检测，Docker 协议检查同步到 104 个工具。

安装：`python -m pip install multisim-mcp==1.3.0rc1`。64 位 MCP 前端仍需配置独立的 32 位 Multisim COM worker。

## 升级须知：从 1.2.0 升级必须重新生成本地模板包

1.3.0 的 `tools/extract_native_component_templates.py` 比 1.2.0 多提取
`probe_element.xml`、`probe_symbol.xml`、`probe_instrument.xml`、`qnpn_model.xml`。
**沿用 1.2.0 生成的模板包不会报错，但会静默降级**：`probe_*` 相关能力不可用，
且仓库自带测试会有 4 个用例因缺少这些模板而失败。两者的 `schema_version` 同为 2，
所以仅比较 schema 版本无法发现问题——这也是 1.3.0rc1 之前 `doctor` 会误报通过的原因。

升级后请重新生成：

```bash
python tools/bootstrap_local_component_pack.py \
  --samples-root "<NI Circuit Design Suite samples>" \
  --output "<模板包目录>" --force
# 然后把 MULTISIM_MCP_TEMPLATE_DIR 指向新目录
```

自 1.3.0rc1 起，`doctor` 会读取 manifest 中的 `generator.version` 并与当前版本比较；
模板包由更旧版本生成时，`schematic.template_pack` 会判为 `fail` 并给出重新生成的提示，
`local_pack.generator_status` 会给出 `stale` / `current` / `newer` / `unknown`。

## English — Upgrading: regenerate the local template pack

The 1.3.0 extractor emits four templates that 1.2.0 did not
(`probe_element.xml`, `probe_symbol.xml`, `probe_instrument.xml`, `qnpn_model.xml`).
Reusing a 1.2.0-generated pack fails **silently**: probe features stop working and
4 repository tests fail on missing templates. Both packs report `schema_version` 2,
so a schema-only check cannot detect it — which is why `doctor` used to pass anyway.

Regenerate after upgrading:

```bash
python tools/bootstrap_local_component_pack.py \
  --samples-root "<NI Circuit Design Suite samples>" \
  --output "<pack directory>" --force
# then point MULTISIM_MCP_TEMPLATE_DIR at the new directory
```

From 1.3.0rc1, `doctor` compares `generator.version` in the manifest against the
running release. A pack built by an older release makes `schematic.template_pack`
report `fail` with a regenerate hint, and `local_pack.generator_status` reports
`stale` / `current` / `newer` / `unknown`.

边界：真实元件实测集中于 Multisim 14.3、LM324AJ 和 2N3904；新图纸仍需人工复核。尚不保证任意复杂电路、容差/温漂/噪声分析、开关电源或多板自动生成。此版本不包含独立桌面前端。

## English

This installable release candidate combines the engineering development branch with PR #13's UTF-8 worker fix. Stable 1.2.0 remains available.

It adds bounded natural-language and composed analog workflows, native Multisim projects, OP/AC/TRAN evidence, topology/model checks and report exports. Component compatibility manifests now ship in the wheel. Licensed NI templates and local experiment outputs are excluded.

Install `multisim-mcp==1.3.0rc1`. Native simulation requires licensed Windows Multisim and a 32-bit COM worker. Real-device evidence is limited to the documented Multisim 14.3 samples; new schematics require visual review. This is not an arbitrary-circuit or production-wide engineering certification.

The independently versioned npm adapter is `multisim-mcp-dsh-plugin@1.3.0-rc.1` and uses the separately installed Python core.

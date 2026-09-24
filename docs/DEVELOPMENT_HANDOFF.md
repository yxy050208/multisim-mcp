# 当前开发落点

以代码、原生实验和本文为恢复依据，不以历史回复中的完成声明代替验收。

- 当前任务：第二阶段的 2N3904 共射原生闭环。
- 已完成：正确输入激励和按目标增益算值、结构预检、原生 OP/AC/TRAN、模型/引脚核查、
  波形专用源模板、共射布局、实际 MCP 执行、报告和 manifest 完整性验证。
- 入口：`run_natural_common_emitter`，预览默认不启动 Multisim；执行要求新输出目录和本地
  VDC/VPULSE 元件模板。`tools/run_common_emitter_plugin_acceptance.py` 可复现实机验收。
- 实测：`sample_validation/ce_mcp_acceptance_final`，细节见 COMMON_EMITTER_NATIVE_ACCEPTANCE.md。
- 下一项：在这个真实可运行入口上加有预算候选比较与完整频带/失真指标；之后扩展电源、
  波形整形。温度/容差、多版本实机认证、完整 CIR 迁移尚未完成。
- 不再采用“最终生成后替换标签就宣布工程正确”的流程；必须让 Multisim 打开、保存、
  回读真实器件属性并导出实际图像，然后核验最终产物的 manifest。

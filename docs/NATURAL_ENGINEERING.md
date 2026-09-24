# 自然语言 RC 工程入口

当前开发入口将文本需求接到已有的生成器和原生实验服务，可连续完成：
需求与假设记录、拓扑生成、元件选值、可编辑 `.ms14`、原生 OP/AC/TRAN、测量验收和报告导出。

首版只覆盖理想、单级、无负载 RC 低通。模型规划已接入，但模型只负责把用户话语整理为有限
RC 需求文本；本地规则、拓扑、元件、仿真和验收仍是权威来源，不能绕过原生验收。
原始需求必须首先被本地入口识别；模型不能把不支持的原始要求改写为一个“最接近”的支持电路。

## 使用

先在项目根目录设置使用已安装依赖的 32 位 Python，并配置本机授权模板包、编解码器和 Multisim COM。

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
$env:MULTISIM_MCP_TEMPLATE_DIR = '本机授权模板包的绝对路径'

# 预览不会启动 COM，也不会创建输出目录。
python -m multisim_mcp.cli natural-engineering --text '设计1kHz RC低通，输入1V，自动选值并导出报告' --output .\sample_multisim\my-rc --json

# 在新目录中执行完整流程。
python -m multisim_mcp.cli natural-engineering --text '设计1kHz RC低通，输入1V，自动选值并导出报告' --output .\sample_multisim\my-rc --execute --json

# 调用配置中的活动模型，检查原始需求一致性，再执行原生实验。
python -m multisim_mcp.cli natural-engineering --planner model --text '设计1kHz RC低通，C=100nF，输入1V，自动选值并导出报告' --output .\sample_multisim\my-model-rc --execute --json
```

也可用 `--input requirements.txt` 读取 UTF-8 需求文件。输出目录必须尚不存在。

MCP 的 `experiment` / `full` 工具集提供：

- `plan_natural_engineering_request(text)`：需求、假设、候选和工程计划。
- `plan_natural_rlc_engineering_request(text)`：生成受限 RLC 二阶低通合同和 SPICE 预览；
  当前只开放规划，不启动原生 RLC 验收。

RLC 的 `native_rlc_acceptance.evaluate_rlc` 已加入原生 AC 矩阵验收层：它会复核复数频响
与二阶模型的一致性，并检查实测峰值频率误差。2026-09-08 已在本机 Multisim 14.3
完成生成 `.ms14`、原生 OP/AC 和 81 点频响回归；10 Ω、10 mH、2.5 µF 的目标约 1 kHz
工程实测峰值为 1 kHz，理论频响最大误差约 2.6e-9。证据目录为
`sample_multisim/rlc_native_acceptance_20260908_fixed2`。当前仍只开放 RLC 规划入口，
已接通 `run_natural_rlc_engineering_request`：它会对候选 R1 逐个执行原生 OP/AC，
保留未达目标但模型一致的候选，并按实测峰值频率选择最佳值。实机多候选证据目录为
`sample_multisim/natural_rlc_auto_20260908_fixed`，共 4 个候选，选中 10 Ω，峰值 1 kHz。
- `run_natural_engineering_request(text, output_dir, execute=false)`：同一流程的预览或执行入口。
- `plan_model_engineering_request(text, provider_config_path=...)`：调用已配置模型，但模型只能调用
  `propose_natural_requirement`，返回内容仍需本地规则验证。
- `run_model_engineering_request(text, output_dir, execute=false, provider_config_path=...)`：模型提出需求后，
  交给同一个本地生成、原生仿真和验收流程。
- `submit_model_engineering_request(text, output_dir, ...)`：将模型工程流程放入 durable job，
  支持排队、进度、取消、失败查询和重试；结果目录仍使用同一个审计格式。

模型提供器使用现有 `MULTISIM_MODEL_PROVIDER_CONFIG` 或 `provider_config_path` 指定的配置。
API 密钥只从配置声明的环境变量读取，不写入计划、工程、报告或错误信息。未配置活动提供器时，
模型入口会明确失败，不会静默退回规则解析或源网表仿真。
CLI 可使用 `--provider-config`、`--provider` 和 `--model-timeout` 指定配置、提供器与超时；
这些选项要求 `--planner model`。模型超时默认 60 秒，上限 120 秒。
模型模式的预览会调用模型，但不会创建目录或启动 Multisim；规则模式的预览不调用模型。

成功执行返回 0；原生执行失败或目标未满足返回 1；需求/路径无效返回 2。
`verification_status` 区分 `unverified`、`passed-supported-rc-contract`、`target-not-met` 和 `failed`。
模型编排模式另外区分 `requirements-rejected` 和 `model-failed`，并返回退出码 1。

## 选值和验收规则

- 输入采用 SI 写法，例如 `R=1.5kΩ`、`C=100nF`、`截止频率为1kHz`、`输入电压为2.5V`。
- 自动选值但未指定电容时，使用 100 nF，并在计划/报告中披露这一假设。
- 未给输入幅值时，采用 1 V，并披露。AC 小信号始终为 1 V，输入幅值作用于 DC 和瞬态。
- 给定 R/C 而未给目标时，由 R/C 推导截止目标。缺少必要参数且未要求自动选值时，要求补充信息。
- 只有要求“选值/优化/自动设计”才展开相邻 E24 候选。“自动仿真”不会更改指定的元件值。
- 每个候选均使用原生工程副本，参数写入后读回，执行 OP、AC、TRAN，再检查原始数据完整性。
- AC 范围为目标的 1/100–100 倍，每十倍频程 40 点。截止频率通过实测半功率点插值。
- 瞬态时长随目标频率缩放，采用延迟脉冲；与有限上升沿解析解逐点比较。
- 原生测量通过参考模型检查后，按实测截止频率选择最佳候选；截止目标允许误差为 2%。
- 无候选达标时保留报告和失败状态，不扩大参数范围、不更换未经测量的值、不调用源网表仿真补成成功。
- 输入范围：截止 10 Hz–100 kHz、电容 1 pF–1 mF、输入幅值 1 mV–100 V、电阻 1 Ω–100 MΩ。
  这是入口的数值校验范围，不等于所有数值组合都已做过实机验收。

额外负载、容差、温度、多板、固定/禁止修改条件、自定义精度或多个频率约束不在首版合同范围内，
入口会拒绝这些已识别的条件。该解析器不是任意中文的完整语义分析器，使用时应检查输出计划和假设。

## 2026-09-08 实机证据

环境：Windows，32 位 Python 3.12，Multisim 14.3，本机授权模板。

| 文本需求 | 结果 | 原生实测 |
| --- | --- | --- |
| 1 kHz RC 低通，输入 1 V，自动选值 | 选中 1.6 kΩ / 100 nF，通过 | 994.577 Hz，误差 0.542% |
| 800 Hz RC 低通，C=100 nF，输入 2.5 V，自动选值 | 选中 2 kΩ / 100 nF，通过 | 795.662 Hz，误差 0.542% |
| 截止 1 kHz，R=1 kΩ，C=100 nF，输入 1 V | `target-not-met`，退出码 1 | 1591.376 Hz，误差 59.138% |

以上分别存于本机 `sample_multisim/natural_auto_rc_20260908`、
`natural_auto_800hz_20260908`、`natural_unmet_20260908`。
共完成 21 项原生分析；两组自动选值各比较三个候选，反例只测给定值。
全部 206 项证据哈希复核通过，800 Hz 工程导出图确认显示 2.5 V、2 kΩ、100 nF。

`report.html` 是汇总入口。`proposal.json` 保留需求、假设、候选、网表和分析命令；
`acceptance.json` 保留判定；`candidate-*/analysis-003.ms14` 是对应候选实际分析时的工程快照。
每次分析目录保留原始矩阵和完整 CSV，`manifest.json` 记录哈希。

不同 Multisim 版本必须重新运行实机测试；本页结果不能视为跨版本认证。

## 原始需求一致性和模型实验留档

- 先解析原始需求，再调用模型。已识别但不支持的负载、精度等约束会在模型调用前拒绝。
- 保留默认值应用前的显式参数，分别比较频率、R、C、输入幅值以及改值权限。
  即使模型省略某个字段后恰好落在同一个默认值上，也判定为遗漏。
- 允许 `1kHz` 与 `1000Hz`、`100nF` 与 `0.1uF` 等等价单位写法；不允许改变本地推导的
  候选、网表、分析命令和验收目标。
- 工具响应必须完整结束、恰好调用一次指定工具，参数只能包含 `text`。截断或额外字段会被拒绝。
- 检查通过后，原生执行仍使用**原始需求文本**，而不是模型重写后的文本。
- 上述校验只覆盖当前 RC 规则识别的需求，不是任意自然语言语义等价的证明。
  缺少必要信息时要求补充，不授权模型自行补充或放宽约束。

模型模式的根目录保存 `input.txt`、`model-plan.json`、`acceptance.json`、`report.html`
和总 `manifest.json`。`model-plan.json` 包含原文、提案、显式参数、契约哈希、差异、
提供器/模型、响应状态及令牌用量。通过后，完整原生实验保存到 `native/`。
模型记录在调用原生实验前写入；需求拒绝、模型连接失败同样保留证据。

2026-09-08 真实联合测试：使用本机已配置的 `ollama-local` / `qwen3:8b-q4_K_M`，
服务未启动的第一次尝试返回 `model-failed`，未进入 Multisim。启动已有 Ollama 服务后，
第二次真实模型调用及原生实验通过，结果保存在
`sample_multisim/model_contract_live_20260908_retry`。

本次模型按要求原文重述了需求，一致性检查无差异；电路拓扑、E24 候选和验收仍由本地代码完成。
三组候选合计 9 项原生分析，选中 1.6 kΩ / 100 nF，截止频率 994.577 Hz，目标误差 0.542%。
模型返回用量为 236 输入、651 输出、共 887 tokens；91 项证据文件哈希复核全部一致。
这证明实际模型 API 与 Multisim 的串联路径已跑通，不代表模型已能独立设计任意电路。

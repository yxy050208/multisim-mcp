# 标准纠错基准 / Standard Correction Benchmarks

`benchmark-suite` 用同一套真实实验、硬性验收、全局搜索与完整性清单，重复验证纠错和
优化核心是否能跨越不同电路族。它不是“任意电路必然可解”的证明，而是防止能力退化为
只对分压器有效的可复现门禁。

## 当前基准

| Case ID | 电路族 | 注入故障 | 验收目标 |
| --- | --- | --- | --- |
| `rc-lowpass` | RC | 电容大一个数量级 | -3 dB 截止频率约 1.592 kHz |
| `rlc-bandpass` | RLC | 串联电阻导致带宽过宽 | -3 dB 带宽约 1.592 kHz |
| `opamp-feedback` | 运放 | 反馈电阻导致闭环增益过低 | 1 V 输入时输出约 11 V |
| `bjt-bias` | BJT | 基极电阻导致过偏置 | 集电极静态电压约 5 V |
| `zener-supply` | 电源 | 串联电阻使稳压管电流不足 | 稳压输出约 5.1 V |

每个用例包含版本化 `CircuitDesign`、明确有限搜索域、硬性要求和预期选择。BJT、稳压管
与运放用例还会验证结构化修改后内联 `.model`/`.subckt` 没有丢失。完成候选必须具有
有限测量和全通过验收；“最接近”但失败的候选不能通过基准。

## 使用

离线验证目录、SPICE 安全编译、模型保真和搜索规范，不启动 Multisim：

```powershell
multisim-mcp benchmark-suite --json
multisim-mcp benchmark-suite --case bjt-bias --case opamp-feedback --json
```

在已安装并授权的 Multisim 工作站运行全部真实实验：

```powershell
multisim-mcp benchmark-suite `
  --run-real `
  --output .\benchmark-results `
  --json
```

输出包含根级 `benchmark-suite.json`、`validation.json`、每个用例的完整全局优化目录，
以及递归 SHA-256 `directory.manifest.json`。摘要会记录套件/用例的 UTC 时间戳、耗时、
通过率和统一验收判据；`validation.json` 让即使所有真实实验都失败，也能生成可核验的
失败证据。目录必须为新目录或空目录，避免把旧证据混入结果。

2026-08-25 的本机真实门禁已完成 5/5 用例；这只记录该受测环境的结果。不同 Multisim
版本、模型或求解器设置仍应重新运行，不能沿用此结论冒充本机证据。

## English summary

`benchmark-suite` is a repeatable cross-family gate over RC, RLC, op-amp, BJT,
and regulated-power circuits. Offline mode validates safe compilation, bounded
search contracts, and inline-model retention. `--run-real` executes the same
cases through real Multisim experiments and writes an integrity-checked suite with
per-case timing, pass-rate, and acceptance metadata. Even an all-failed run retains
`validation.json` and a verifiable directory manifest.
The benchmark demonstrates tested coverage, not guaranteed repair of every
possible circuit.

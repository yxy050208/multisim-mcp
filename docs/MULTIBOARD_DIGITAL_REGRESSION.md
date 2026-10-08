# 双板复杂数字回归

`tools/run_multiboard_digital_regression.py` 是单板数字矩阵之外的多板闭环入口。案例 `split_logic_load` 将一个数字链拆到 `logic` 与 `io` 两块板：源板产生时钟和数据，远端板用显式边界电压夹具复现这两个输入，再经过 NOT/AND 级联驱动 1 kΩ 负载。J1 使用已经在 Multisim 14.3 实机验收的 `HDR1X4`，四针固定为：

| 针脚 | 网络 | 作用 |
| --- | --- | --- |
| P1 | `clk` | 数字时钟跨板信号 |
| P2 | `data` | 数字数据跨板信号 |
| P3 | `high` | 共享数字电源 |
| P4 | `0` | 共享回流 |

远端板的输入夹具不是推断出来的。它们在请求中明确声明为 `voltage_source`，波形与源板一致，因此每块板可以独立打开、保存、重开和仿真，同时仍能比较跨板接口和完整设计参考。缺少这样的边界条件时，独立板级仿真中的浮空输入不能被误判成跨板连线正确。

## 本机实机运行

需要 32 位 Python、Multisim 14.3 和本地授权模板包。模板包、`.ms14`、原生 XML、截图和 CSV 只写入证据目录，不提交仓库：

```powershell
$env:PYTHONPATH = "mcp_server"
$env:MULTISIM_MCP_TEMPLATE_DIR = "C:\Users\18331\Documents\multisim-evidence\local-pack-20261003"

python tools/run_multiboard_digital_regression.py `
  --output C:\Users\18331\Documents\multisim-evidence\multiboard-digital-20261009-dc-v6 `
  --case split_logic_load --analysis dc --target-version "Multisim 14.3" --execute

python tools/run_multiboard_digital_regression.py `
  --output C:\Users\18331\Documents\multisim-evidence\multiboard-digital-20261009-tran-v5 `
  --case split_logic_load --analysis tran --target-version "Multisim 14.3" --execute

python tools/run_multiboard_digital_regression.py `
  --output C:\Users\18331\Documents\multisim-evidence\multiboard-digital-20261009-ac-v1 `
  --case split_logic_load --analysis ac --target-version "Multisim 14.3" --execute
```

不带 `--execute` 时只做 COM-free 预览，输出根目录的 `matrix.json` 会显示结构化合同、fixture 覆盖和版本化连接器映射，但仍标记为 `logical-only/unverified`。

## 验收门禁

每个分析都要求两块板完成生成、保存重开、原生器件完整性、布局和 `ReportNetlist` 拓扑检查；`HDR1X4` 的 P1–P4 逐针连接也必须通过。DC 比较工作点，TRAN 和 AC 保存完整原生序列并比较接口及完整设计参考。

Multisim 的瞬态求解器会为独立板选择不同的自适应采样轴。严格比较仍是默认行为；本案例对 TRAN/AC 显式使用 `series_alignment=linear`，只在轴单调、覆盖相同分析区间且样本完整时，将远端序列线性重采样到基准板的原生轴。结果会在 `comparison_basis` 中记录这一事实，不能把这种比较当成连续时间等价证明。

## 2026-10-09 实测结果

在 Windows 11、Multisim 14.3、本地 `local-pack-20261003` 模板包上，`split_logic_load` 的 DC、TRAN、AC 均为 `accepted/native-verified`。三次运行的门禁均为真：

- 两块板生成、保存、重开和输出探针完整；
- 两块板 `ReportNetlist` 拓扑和布局通过；
- `HDR1X4` 映射为真实原生连接器，P1–P4 针网关系通过；
- 跨板 `clk`、`data` 接口与完整参考一致；
- 远端 `io_out` 及 1 kΩ 负载保留并参与仿真；
- DC、TRAN、AC 的完整采样结果和实验报告均已写入证据目录。

这些结果只证明该案例在 Multisim 14.3 上的闭环，不外推到其他连接器、元件型号或 Multisim 版本。目标版本没有独立 manifest 或实机验收时，运行器仍会失败关闭。

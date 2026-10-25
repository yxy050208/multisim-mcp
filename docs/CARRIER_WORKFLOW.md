# 载体工作流：从本机 Multisim 设计提取可仿真的原生器件

> Harness for making any database component available to the MCP. English summary at the bottom.

## 什么时候需要这个流程

MCP 生成原理图靠**克隆载体模板**，而不是调用 Multisim 的放置接口（COM 没有放置 API）。
模板包随附的载体来自 NI 样例；不在样例里的器件（例如 555 定时器、74 系列芯片）
需要从**你自己的授权设计**中一次性提取。提取后：

- 生成器可以放置该器件；
- `prepare_simulation_netlist` 会自动把载体内嵌的 SPICE 模型渲染进命令引擎网表，
  器件即可批处理仿真并出具波形证据。

## 步骤

1. **在 Multisim GUI 放置器件**：新建空白图 → 从数据库放置目标器件（例如
   `Mixed / TIMER / LM555CN`）→ 普通保存为 `.ms14`。
   - 优先选**数据库器件**而非"交互式虚拟/额定"器件：后者（Basic 组的
     "Rated 555 Timer" 等）只为交互仿真设计，批处理验证会退化。
   - 一张图可以放多个不同器件，用 `--refdes` 逐个提取。
2. **提取并装入本地包**（原模板自动备份，哈希记入 manifest）：

   ```powershell
   <python32>\python.exe tools/overlay_local_component_pack.py `
     --pack "C:\MultisimMcp\component-pack" `
     --source <你的设计>.ms14 `
     --refdes U2 --kind TIMER8 --identity-token LM555CN --force
   ```

   - 不知道身份串？随便填一个再运行——报错会列出该元件的实际身份候选。
3. **验证**：

   ```powershell
   <python32>\python.exe -m multisim_mcp.cli doctor --lang zh
   ```

   `TIMER8` 从缺失列表消失即成功。

## 命令引擎的行为

`X... TIMER8` 这类网表行会被 `prepare_simulation_netlist` 自动渲染：命名端子
（`%tGND`、`%t~1Q`）映射为网表节点，模型体（`.subckt` / `.MODEL`）内嵌一次。
实测 LM555CN 无稳电路（RA=1k, RB=4.7k, C=100n）：振荡 1309 Hz，理论 1385 Hz（+5.5%，
真实宏模型正常水平），日志零错误。

## 已知边界

- 多段器件（74xx 的 A/B 段）当前按 A 段提取；
- 交互式器件的输出流无法经 COM 采集（`OutputReady` 仅首窗触发），
  这类器件只适合 GUI 交互仿真；
- `ReportNetlist` 可能省略宏内部体，原生存在性以 `EnumComponents` 为准。

---

## English summary

Place the database part in the Multisim GUI, save normally, then run
`tools/overlay_local_component_pack.py --pack <pack> --source <design.ms14>
--refdes <ref> --kind <KIND> --identity-token <part>` to install the carrier.
Prefer database parts over interactive "rated virtual" ones — the latter only
simulate in Multisim's interactive engine. In the command netlist,
`prepare_simulation_netlist` renders carrier X-lines from the extracted
templates (named tokens → nets, model body embedded once), so the part is
batch-simulable with verifiable waveforms. If you do not know the identity
token, pass any value: the error lists the component's actual identity strings.

# dsl-plan 工单总索引

Version: v3.0
Created: 2026-08-25
Owner-Flow: autofw 混合编辑器（DSL + 流程图双向同步）
Source-Docs: ../混合编辑器实施计划.md（基线 994 行 / 73,224B / 2026-08-25 10:19 版）
Executor-Profile: 弱模型（小上下文）施工，强模型仅做 P-1 工具预置与本表维护
Session-Recipe: 单会话只喂本文件 + 当前 1 份 WO 全文；WO 正文硬上限 450 行；禁追加其他文档
Budget-Rule: 各 WO 的 BUDGET 只计正文（`<!-- APPENDIX-START -->` 截断）；正文行数由 tools/check_plan.js budget 子命令机械核对

---

## 0. 与源文档的冲突检查结论（2026-08-25）

他方 Agent 于 08-25 10:19 对《混合编辑器实施计划.md》追加了尾部内容，经逐锚点比对：

| 区域 | 基线 | 现状 | 判定 |
|---|---|---|---|
| 主体规格 L24–L823（决策表/词法/BNF/映射/原子清单/T1–T5） | — | 全部锚点行号零漂移 | 未受影响 |
| §13 协作协议 | 13.1–13.3 | 新增 13.4 上下文纪律 / 13.5 迭代上限 / 13.6 交付物清单 | 采纳，与本工单 W6/W12 同向 |
| 文末 v0.5 进度块 | 自述"起草中·未完"+A/B/C/D/E 待续清单 | 同块扩写（B1–B10/C1–C12/D1–D3 具体化） | 采纳，作为勘误依据来源 |
| 修复熔断次数 | L4=2 次 / L962=3 次 / L992=2 次 | 三处不一致 | **裁决统一为 2 次**（见 D-R1b） |

结论：无冲突错乱，属追加式扩展。本工单体系基于 994 行版编制；后续若再发现源文件变动，
先重跑 `node tools/check_plan.js grep "指向循环外|指向循环体外" ../../混合编辑器实施计划.md`
并复核本节锚点表，再继续施工。

## 1. 五项用户裁决（2026-08-25 定稿，执行模型不得重开）

- **D-R1a** wait 超时语义：选 **A（失败）**。wait 类原语超时 = 运行时错误 **R307**，
  阻断当前流程运行；同步期（dsl_to_flow/flow_to_dsl）不校验运行结果，只校验参数形态。
- **D-R1b** 修复熔断次数：统一为 **2 次**。同一测试连续 2 次修复不过 → BLOCKED 上报，
  附两次尝试 diff 与完整错误输出；禁止第 3 次尝试。源文档 L962 的"3 次"按本条修正。
- **D-R1c** break 边表示：break 出边**指向其所在循环 body 子图的 end 节点**，label=`break`，
  预览图可见但不跨子图；buildRunFlow desugar 时消除该边（与源文档文末 B7 定稿一致，
  勘误 #03 的残留扫描仍须覆盖全部旧表述变体）。
- **D-R1d** T5 数据来源：**附录 A 重构版**。《混合编辑器任务书.md》不存在（全盘已核）、
  MEMORY.md 暂存版不可达，故 03_WO_P0 完成后由执行模型按附录 A 规格重建 T5 DSL 全文；
  若重建后结构断言（29 节点/6 图/5 键）不符，以实际解析结果为准回填修正断言并记 PROGRESS ISSUES。
- **D-R1e** 运行时错误码分离：采用 **R306/R307** 双码。R306=子图引用缺失/悬空（运行图构建期），
  R307=wait 超时（运行期）；两者均不进入同步期错误域（E1xx/E2xx/E3xx 保持纯静态/同步语义）。

## 2. 执行顺序（串行，禁止跳步）

```
P-1  强模型一次性动作（已完成，见 §4 工具预置记录）
P0   只读侦察 + 探针        03_WO_P0.md    （原 v0.5 计划的 Phase 0，前移并入本体系）
P1   dsl_parser.js          04_WO_P1a.md + 04_WO_P1b.md （两卡交付）
P2   dsl_to_flow.js         05_WO_P2.md
P3   flow_to_dsl.js         06_WO_P3.md
P4   export_md.js           07_WO_P4P5.md （含 P5 打磨）
ACC  机械验收               08_ACCEPTANCE.md （逐条命令，FAIL 即 FAIL）
```

每张卡开工前：读 01_PROTOCOL.md → 在 PROGRESS.md 置 doing → 施工 → 自检命令全过 → 置 done。
任何一步 FAIL：按 01_PROTOCOL.md 第 6 节 SOP 处置，禁止自行修改脚本或放宽断言。

## 3. 文件清单

- 00_INDEX.md
- 01_PROTOCOL.md
- 02_REDMILL.md
- 03_WO_P0.md
- 04_WO_P1a.md
- 04_WO_P1b.md
- 05_WO_P2.md
- 06_WO_P3.md
- 07_WO_P4P5.md
- 08_ACCEPTANCE.md
- PROGRESS.md
- APPENDIX_A_T5_DSL.md
- tools/check_plan.js

## 4. 工具预置记录（P-1，强模型已完成）

| 动作 | 证据 |
|---|---|
| tools/check_plan.js 创建 | 本目录 tools/check_plan.js，Node >=16 零依赖 |
| 七个子命令实测 | 见 09_AUDIT_REPORT.md §脚本实测（split/grep/errata/inline/budget/progress/version） |
| 正反夹具测试 | 见 09_AUDIT_REPORT.md §夹具验证 |

## 5. 会话配方（硬上限）

1. 每个施工会话输入 = 00_INDEX.md（本文件）+ 当前 WO 一份，此外不给任何文档。
2. WO 正文 >450 行即为编写事故：退回强模型拆卡，施工模型不得自行摘要或跳读。
3. 附录区（`<!-- APPENDIX-START -->` 之后）仅在 WO 明确指示时读取对应小节。
4. 输出一律走 [DELIVER] 固定模板（各 WO 文末），一问一答，禁止自由发挥。

## 6. 红线（继承 R0' 并扩展）

- 只允许写入 `docs/dsl-plan/**` 与 `gui/dsl_*.js`、`gui/flow_editor.html`（带 .bak-P<N> 备份）。
- 修改 `gui/flow_editor.html` 或 `gui/server.py` 前必须 PRE-WRITE（§13.3 规程不变）。
- 禁止改 `core/**`、`adapters/**`、`state/**`；探针阶段对 server.py 只读。
- 禁止创建/修改 `tools/check_plan.js`；发现脚本缺陷 → BLOCKED 上报，由强模型处置。

## 7. 变更日志

- v3.0 (2026-08-25): 基于 R0'v2 审核（F1–F13 事实核验 + W1–W18 小模型适配）重构；
  P0 并入；勘误扩至 25 条；五项裁决定稿；新增 PROGRESS/ISSUES 断点续跑协议；
  check_plan.js 合并为单一工具七子命令。

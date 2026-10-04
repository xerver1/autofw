# R0'v3 审核与重构总报告

Plan-Doc-Version: v3.0
审核对象：《混合编辑器实施计划.md》994 行基线 + R0'v2 拆分方案
证据链：S1 勘察探针 / probe_plan_v05.py / probe_conflict.py / probe_conflict2.py /
事实核验 F1–F13（subagent_facts_verify.md）/ 小模型适配 W1–W18（subagent_weakmodel_adapt.md）

---

## 一、冲突检查结论（用户问的第一件事）

他方 Agent 于 08-25 10:19 修改了《混合编辑器实施计划.md》（69,181B/977 行 → 73,224B/994 行）。
逐锚点比对结论：**没有冲突错乱，属追加式扩展**。

| 区域 | 基线 | 现状 | 影响 |
|---|---|---|---|
| 主体规格 L24–L823 | 决策表/词法/BNF/映射/T1–T5 全套 | 全部锚点行号零漂移 | 此前全部审查结论继续有效 |
| §13 协作协议 | 仅 13.1–13.3 | 新增 13.4/13.5/13.6 三小节 | 与本工单 W6/W12 同向，采纳 |
| 文末 v0.5 进度块 | "起草中·未完"+待续清单 | 同块扩写（B1–B10/C1–C12/D1–D3 具体化） | 作为勘误依据来源采纳 |
| 熔断次数口径 | — | L4=2 次 / L962=3 次 / L992=2 次 | **新增矛盾**，裁决统一 2 次（勘误 #12） |

## 二、R0'v2 阻断级问题（4 条，全部在 v3 中修复）

<div class="rich-grid">

<section class="rich-card">
<div class="rich-card-title">① DoR 死锁</div>

DoR 要求 docs/facts F-A0–A5 就绪，但 P0 从未执行、目录不存在 → 开工即 BLOCKED。
**修复**：P0 探针并入为第一张卡（03_WO_P0），产物改存 docs/dsl-plan/facts/。
</section>

<section class="rich-card">
<div class="rich-card-title">② 悬空引用群</div>

7.2/7.3 小节不存在（D9"见 7.2/7.3"悬空）；任务书不存在但被引 6 处；
T5 DSL 全文缺失；P1a tokenizer 无复用源。**修复**：定性改"新增"；
数据源定稿附录 A 重构版；P1a 明确新建并全文内联规格。
</section>

<section class="rich-card">
<div class="rich-card-title">③ 附录四脚本不可运行</div>

`new RegExp©` 版权符损坏、省略号字符入正则、全角引号匹配半角源码永不命中、空分组、WARN 泛洪、#### 标题漏检。
**修复**：废弃四脚本，合并为 tools/check_plan.js 单工具七子命令，
由强模型预创建并实测（P-1 已完成），施工模型禁建禁改（负面清单第 2 条）。
</section>

<section class="rich-card">
<div class="rich-card-title">④ 小模型会话超载</div>

原配方"协议+WO"无硬上限；附录不计 BUDGET 被误解为不进上下文；无断点续跑。
**修复**：WO ≤450 行硬上限+超限拆卡（P1→P1a/P1b）；BUDGET 只计正文（APPENDIX-START 截断）；
PROGRESS.md 机器可读账本+[RESUME] 续跑协议；术语表 18 词；负面清单 11 条。
</section>

</div>

## 三、五项裁决定稿（用户裁决 2026-08-25）

| 裁决 | 定稿 |
|---|---|
| D-R1a wait 超时 | =运行时错误 R307，阻断当前流程（选 A） |
| D-R1b 熔断次数 | 统一 **2 次**，第 2 次失败即 BLOCKED |
| D-R1c break 边 | 指向所在循环 body 子图 end，label=break，desugar 时消除 |
| D-R1d T5 数据源 | 附录 A 重构版重建；断言以实测回填修正 |
| D-R1e 错误码分离 | R306=子图引用缺失，R307=wait 超时，不入同步期错误域 |

## 四、勘误 25 条概览

25 条全表见 [02_REDMILL.md](02_REDMILL.md)。
按严重度分布：**阻断 4 条**（#2/#5/#6/#11）、**高危 9 条**（#1/#3/#7/#8/#12/#13/#16/#19/#20）、**中危 12 条**。
相对 R0'v2 的 23 条：新增 #12（熔断矛盾，本次冲突检查新发现）、#24（快捷键双注册验收）、
#25（perf 阈值定稿 500 节点解析 ≤800ms）；删除原 #04"逐字采用"错误表述（并入 #2/#4）。

## 五、验证记录

### 5.1 工具实测（node 实机运行，2026-08-25）

```
split    exit=0   清单提取 12 文件全部存在且 ≤450 行; tools/check_plan.js 在位
errata   exit=0   勘误条数一致: 25 条, 无空列
inline   exit=0   5 个 WO/验收文件 0 外部引用
budget   exit=0   各卡正文均在预算内 (03_WO_P0 71/120; 04_WO_P1a 84/260; 05_WO_P2 87/300)
progress exit=0   DATA {"total":7,"todo":6,"doing":0,"done":1,"blocked":0}
version  exit=0   11 个文件版本一致 v3.0
grep     exit=1   对源文档命中 3 处 break 旧表述 (L378/L416/L538) —— FAIL 路径真实有效
```

### 5.1b 夹具正反用例（run_fixture.py，证明 FAIL 路径可触发）

```
反例 A  BIG.md 451 行 > 450 上限      → exit=1 [FAIL] 行数 452 超硬上限 450
反例 B  勘误表声明 26 实际 25 条      → exit=1 [FAIL] 条数不符: 声明 26, 实际 25
反例 C  卡片 blocked 但 ISSUES 空     → exit=1 [FAIL] blocked 但 ISSUES 区无对应记录
正例 D  夹具版本一致 v9.9             → exit=0 全部 OK
```

原始输出存 fixture_result.txt；夹具临时目录 _fixture 已清理。
另修复自查缺陷一处：budget 附录截断原为子串匹配会误伤正文提及时，已改为整行精确匹配 `<!-- APPENDIX-START -->` 并复测通过。

### 5.3 未覆盖项（如实申报）

- D1/D2 运行接线冒烟需 GUI 服务在线，属施工期动作，本次未启动服务；
- T5 最终数值依赖 P0-A5/A6 实测回填（D-R1d 设计如此）；
- 反方审查 subagent 因额度 402 两次失败未产出——由主线探针复核补位（probe_conflict*.py 即其职责范围）。

## 六、交接与回滚

- **使用方式**：施工会话只喂 00_INDEX.md + 当前 WO；按 §2 执行顺序串行推进；
  每卡开工置 doing、收尾跑 progress 自检 + [DELIVER]。
- **回滚方式**：工单体系全部位于 docs/dsl-plan/** 新增目录，删除该目录即完全回滚，
  不触碰 gui/core/state 任何既有文件；源文档本身零修改。
- **风险项**：
  1. T5 数值回填依赖 P0 实测，若推演与原子清单规则有出入需二次回填（已在 D-R1d 预留）；
  2. 弱模型对 450 行 WO 的执行质量仍需 P0/P1a 两卡实测校准，必要时再拆细；
  3. check_plan.js 的 grep 子命令是字面子串匹配（非正则），更复杂模式需拆多次调用。

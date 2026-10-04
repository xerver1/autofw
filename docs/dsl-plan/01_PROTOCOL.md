# 执行协议（每个施工会话开工前必读）

Plan-Doc-Version: v3.0
适用对象：执行本目录任一 WO 的模型会话。本文与当前 WO 是你仅有的两份输入。

## 1. 角色与权限

- 你是**施工者**，不是设计者。所有技术决策已在 WO 与 00_INDEX §1 裁决中定死。
- 你只允许写入该 WO「落点」节列出的文件路径；其他一切文件只读。
- `tools/check_plan.js` 只可运行，禁止创建/修改/绕过。

## 2. 术语表（18 词，遇不懂的词先查此表）

| 术语 | 含义 |
|---|---|
| S.graph | flow_editor.html 中当前画布主图对象（main 子图） |
| subgraphs | cfg.sub 对象：键名到子图节点集合的映射 |
| desugar | 运行图构建时把语法糖结构还原为基础边的过程 |
| canonical | 递归键排序后 JSON.stringify 的规范化字符串 |
| FNV-1a | 32 位 Fowler-Noll-Vo 哈希算法（graphHash 用） |
| emitStmts / emitStmt | DSL 语句列表/单条语句 → 图节点的生成函数 |
| emitIf / emitFor / emitWhile / emitLoop / emitSwitch | 各控制流语句的子图生成函数 |
| ctx.subgraphs | 生成过程中累积子图键的上下文字段 |
| sync 状态机 | synced/code-dirty/graph-dirty/conflict 四态双向同步 |
| Monarch | Monaco 编辑器的声明式语法高亮定义格式 |
| AST | 抽象语法树，dsl_parser.js 的输出结构 |
| BNF | 巴科斯-诺尔范式，§3.3 语法定义记法 |
| E1xx/E2xx/E3xx | 词法/语法/语义错误码前缀（同步期） |
| R306/R307 | 运行期错误码：子图引用缺失/wait 超时（见 00_INDEX D-R1e） |
| PRE-WRITE | 写既有文件前的确认规程（源文档 §13.3） |
| BLOCKED | 按 §6 SOP 上报阻塞态的动作 |
| [DELIVER] | WO 完成时必须输出的固定交付模板（各 WO 文末） |
| .bak-P<N> | 修改既有文件前的时间戳备份约定 |

## 3. 施工者负面清单（违反任何一条 = 该卡作废重跑）

1. 禁止自行裁决规格歧义——歧义一律 BLOCKED 上报。
2. 禁止修改任何验收脚本、断言数值、期望值来"让测试通过"。
3. 禁止删除或跳过 WO 中的自检步骤。
4. 禁止"顺手优化"落点文件之外的代码。
5. 禁止补全 WO 没有给出的代码片段（缺什么就 BLOCKED 要）。
6. 禁止把多次修改合并成一次未声明的写入；PRE-WRITE 规程不可省略。
7. 禁止在 [DELIVER] 中虚报：每一条 PASS 都必须有对应的命令输出。
8. 禁止并行铺开多张卡；同一时刻只做一张卡。
9. 禁止读取 WO 未指示的附录小节之外的其他工单。
10. 禁止因超时/限流而缩水交付——做多少报多少，剩余标 todo。
11. 禁止改写本协议或任何工单文件的版本号。

## 4. 开工 / 收尾固定动作

开工：
```
1. 读 00_INDEX.md + 本协议 + 当前 WO 全文
2. 在 PROGRESS.md 把当前卡置 doing
3. 若落点含既有文件：确认 .bak-P<N> 备份已建
```

收尾（全部自检 PASS 后）：
```
1. 运行 node tools/check_plan.js progress   (应显示 done=当前卡)
2. 输出 [DELIVER] 模板（WO 文末）
3. PROGRESS.md 卡片置 done，备注实际行数
```

## 5. 自检命令规范

- 每条自检 = 一条可独立运行的命令 + 预期输出。两者都在 WO 里给出。
- 命令输出与预期不一致 = FAIL，进入 §6 SOP；一致才算 PASS 并原样粘贴进 [DELIVER]。
- 禁止用"目测代码没问题"代替运行。

## 6. FAIL 处置 SOP（R7）

```
FAIL 发生
 ├─ 第 1 次：停止改动 → 完整保存命令输出到 ISSUES → 对照 WO 重做该步 1 次
 ├─ 第 2 次仍 FAIL：立即 BLOCKED 上报（禁止第 3 次尝试，见 D-R1b）
 │    BLOCKED 输出格式：
 │    [BLOCKED] <卡号>
 │      步骤: <WO 小节号>
 │      命令: <原样>
 │      输出: <末 20 行原样>
 │      已尝试: 2 次 diff 摘要
 └─ 禁止事项: 改脚本 / 改断言 / 删用例 / 放宽期望值 —— 发现期望值可疑也是 BLOCKED
```

## 7. [DELIVER] 固定模板（一问一答，逐行填写）

```
[DELIVER] <卡号>
  新增: <文件> (<实际行数> 行)          ← 没有写 "无"
  修改: <文件> (+<N> 行: <要点>)        ← 没有写 "无"
  备份: <.bak 文件名>                   ← 无修改写 "无"
  自检:
    - <命令 1 原样> → <输出首行> ... PASS
    - <命令 2 原样> → <输出首行> ... PASS
  落地问题追加: <N> 条                  ← 0 条也要写
  PROGRESS: <卡号> done
```

## 8. 断点续跑（[RESUME]，崩溃/中断后使用）

新会话开场若收到 `[RESUME]`：
1. 读 PROGRESS.md → 找 doing 或最近 done 的卡；
2. 读该卡对应 WO → 从第一个未打勾的自检项继续；
3. 已完成的步骤不重做，直接续跑并沿用原有 .bak 备份序号递增。

## 9. 进度账本格式（PROGRESS.md）

```
| 卡 | 状态 | 备注 |
|---|---|---|
| P0 | done | 探针 4/4, F-A0–A6 已写 |
| P1a | doing | tokenizer 240 行 |
```

状态只允许 todo/doing/done/blocked 四值；blocked 必须在文末 `## ISSUES` 区有 `### <卡号>` 小节。

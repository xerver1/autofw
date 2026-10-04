# WO-P1b：dsl_parser.js — 语义检查与 AST 定稿（新建，≤250 行）

Plan-Doc-Version: v3.0
BUDGET: 220 行正文
前置：P1a done
落点：gui/dsl_parser.js（追加语义层）与 gui/test_dsl_parser.js（追加 T3' 用例）
备份：.bak-P1a（对 P1a 版 dsl_parser.js）
输入规格：本 WO 内联；源文档只允许查 §4.1 AST Schema 一节

## 1. 目标

在 P1a 词法/语法层之上补齐：两遍扫描的 sub 名集合消费、call 目标校验、
break 越位检查、AST 输出与 §4.1 Schema 的逐字段一致性。

## 2. 语义规则（内联全文）

1. **前向调用**：第一遍收集全部 `sub <名>` 定义；第二遍遇 `call <名>` 查集合，
   未定义报 E205-call（勘误 #20 两遍扫描定稿）。
2. **break 归属**：维护循环深度栈；`break` 时栈空 → E205-break。
   AST 中 break 节点记录其所属最近循环 id（供 P2 break 边指向 body end，D-R1c）。
3. **case 空分支**：允许为空但必须显式写 `pass` 或注释说明 → 否则 E209；
   switch 缺 default 允许（P2 侧生成空 default 子图 + warning）。
4. **重复定义**：同名 sub 定义两次 → E205-dup。
5. **AST 字段**（与源文档 §4.1 对齐）：节点一律 `{kind, line, ...}`；
   if: `{kind:'if', cond, then:[], else:[], line}`；
   loop: `{kind:'for'|'while'|'loop', var?, times|cond|count, body:[], line}`；
   switch: `{kind:'switch', on, cases:[{when,body}], default, line}`；
   sub: `{kind:'subdef', name, body, line}` / `{kind:'call', name, line}`。

## 3. 追加测试 T3'（语义用例，直接落 test_dsl_parser.js CASES 尾部）

```
{src:"call foo", expect:["E205"]}                      // call 未定义(无 sub foo)
{src:"sub a\nend\ncall a", expect:[]}                  // 前向调用 OK (先收集后检查)
{src:"if x\nbreak\nend", expect:["E205"]}              // 循环外 break
{src:"for i in 1~3\nbreak\nend", expect:[]}            // 循环内 break OK
{src:"", expect:["E206"]}                              // 缺 main 体
{src:"switch a\ncase 1\nend", expect:["E209"]}         // case 空且无 pass
```

## 4. 自检命令

```
1. node gui/test_dsl_parser.js        预期: "PASS n/n" 且总数 ≥ P1a 数 + 6
2. node tools/check_plan.js progress  预期: 无 FAIL 行
```

## 5. [DELIVER] 模板

```
[DELIVER] P1b
  新增: 无
  修改: gui/dsl_parser.js (+N 行), gui/test_dsl_parser.js (+6 条)
  备份: gui/dsl_parser.js.bak-P1a
  自检: <两条命令输出>
  落地问题追加: N 条
  PROGRESS: P1b done
```

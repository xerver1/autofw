# WO-P1a：dsl_parser.js — 词法与语法（新建，≤300 行）

Plan-Doc-Version: v3.0
BUDGET: 260 行正文
前置：P0 done（facts/ 六份在位）
落点：gui/dsl_parser.js（新建）；test_dsl_parser.js 由本卡一并创建于 gui/
备份：不适用（纯新建）
输入规格：本 WO 内联全部所需规格；原文档只允许查 §3.1 词法表、§3.3 BNF 两节（行号见文末锚点）

## 1. 文件骨架（照此创建，函数签名不得改名）

```js
// gui/dsl_parser.js  — 纯函数, 无 DOM, Node 与浏览器双可用 (UMD 三行导出)
'use strict';
function stripComments(src) { /* 去 // 行注释与 /* */ 块注释 */ }
function mergeContinuedLines(src) {
  // 折行合并: stripComments 后执行; 括号深度>0 时合并下一行;
  // 诊断行号取折行首行 (勘误 #19); 返回 {lines: [{text, line}]}
}
const NUMBER_TIME = /[0-9]+(ms|s)(~[0-9]+(ms|s))?/;   // 含区间 ~ (文末 B2)
const KEYWORDS = ['if','else','for','while','loop','switch','case','default','break','sub','end','return','log','warn'];
function tokenize(lines) { /* → [{t:'NUM'|'STR'|'ID'|'KW'|'OP'|'TIME', v, line}] */ }
function parseProgram(tokens) { /* 两遍扫描: 第一遍收集 sub 名集合, 第二遍建 AST (勘误 #20) */ }
function parseStmt / parseIf / parseFor / parseWhile / parseLoop / parseSwitch / parseSub ...
function diag(code, line, msg) { /* → {code, line, msg}; code 见第 4 节错误码表 */ }
module.exports = { stripComments, mergeContinuedLines, tokenize, parseProgram, diag };
```

## 2. 词法规则（全文以此为准）

- 标识符：`[A-Za-z_][A-Za-z0-9_]*`；字符串：`"..."` 支持 `\"` `\\` `\n` 转义；
- 时间字面量：`<n>(ms|s)` 及闭区间 `<n>(ms|s)~<n>(ms|s)`（如 `1s~2s`）；
- 数字：整数与小数；运算符：`( ) [ ] , : = == != < > <= >= + - * /`；
- 注释先行剥离；括号深度 >0 的行尾 `\` 或裸换行均合并到下一行。

## 3. 语法要点（BNF 全文以源文档 §3.3 为准，此处只列易错点）

1. `log` 与 `warn` 等价：`("log"|"warn") expr`，括号被表达式收集（B1 定稿）；
2. if/for/while/loop 用 end 收尾；switch 用 end 收尾，case/default 分支体可为空；
3. break 只允许出现在循环体或 case 分支内，否则 E205；
4. sub 定义 `sub <名>` ... `end`；调用 `call <名>`；main 体缺省存在；
5. 缺 main → **E206**；未知语句首词 → **E107** 阻断同步并跳到行尾继续（B4 错误恢复）。

## 4. 错误码表（同步期；R306/R307 不属于本文件）

| 码 | 类 | 触发 |
|---|---|---|
| E101–E104 | 词法 | 非法字符/未闭合字符串/非法时间区间/非法数字 |
| E105 | 语法 | 缺 end / end 不匹配 |
| E106 | 语法 | 表达式不完整 |
| E107 | 语义 | 无法识别的语句首词（阻断同步） |
| E201–E205 | 结构 | 缺 main(E206 除外)/break 越位/case 空(E209)/重复定义/call 未定义 |
| E206 | 结构 | 缺 main 函数体 |
| E208 | 结构 | end for / end while 类型错配 |
| E209 | 结构 | case 分支为空且无注释说明 |

多错误同报：单次解析最多收集 10 条后停止；每条都带原始行号（折行取首行）。

## 5. 测试用例 T1/T2/T3

直接复制源文档 §6.1（T1 词法）、§6.2（T2 控制流）、§6.3（T3 错误 12 条）三节用例表全文到
gui/test_dsl_parser.js 的 CASES 数组——**逐条含期望值**，禁止"见 §6.x"式引用落稿。
若复制时发现用例与本 WO 第 4 节错误码冲突，以本表为准并在 ISSUES 记一条。

## 6. 自检命令

```
1. node -e "require('./gui/dsl_parser.js')"          预期: 无异常退出 (exit 0)
2. node gui/test_dsl_parser.js                       预期: 输出 "PASS n/n", 失败即 FAIL
3. node tools/check_plan.js progress                 预期: 无 FAIL 行
```

## 7. [DELIVER] 模板

```
[DELIVER] P1a
  新增: gui/dsl_parser.js (<实际行数>/300 行), gui/test_dsl_parser.js
  修改: 无
  备份: 无
  自检: <三条命令输出>
  落地问题追加: N 条
  PROGRESS: P1a done
```

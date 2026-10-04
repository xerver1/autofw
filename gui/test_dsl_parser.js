// gui/test_dsl_parser.js — P1a 验收: T1/T2 全量 + T3 #1–#7 (码号依 WO§4/主审裁决)
'use strict';
const { stripComments, mergeContinuedLines, tokenize, parseProgram } = require('./dsl_parser.js');

function parse(src) {
  const stripped = stripComments(src);
  const { lines } = mergeContinuedLines(stripped);
  const lexDiags = [];
  const toks = tokenize(lines, lexDiags);
  const ast = parseProgram(toks);
  ast.diagnostics = lexDiags.concat(ast.diagnostics).slice(0, 10);
  ast.ok = ast.diagnostics.length === 0;
  return ast;
}

const T1 = `// 行注释
/* 块注释
   跨行 */
let name = "主界面"
let x = 100
let arr = [1, 2, 3]
delay 1s~2s
find color #FF3300 in (100,200,500,600) sim 0.95
`;

const T2 = `main
  let hp = 100
  if hp < 30 then
    call 使用药水()
  else if hp < 60 then
    delay 2s
  else
    log "状态正常"
  end if
  for i = 1 to 10 step 2
    click (500 + i * 10, 300)
    delay 200ms
  end for
  while exists image "loading.png" threshold 0.8
    delay 500ms
  end while
  loop
    click image "next_btn.png"
    delay 1s
    if find image "done.png" threshold 0.9 then
      break
    end if
  end loop
  switch stage
    case 1: call 第一关()
    case 2: call 第二关()
    default: log "未知关卡"
  end switch
end main
`;

// 期望值: 依底稿 §6.1/§6.2/§6.3, 但 T2 的 ok=true 与 main.stmts=5 按 WO§4/§5 覆盖(见 ISSUES)
const CASES = [
  { name: 'T1 词法', src: T1, expect: { ok: true, diagCodes: [], mainStmts: 5, maxDepth: 0, subs: 0 } },
  { name: 'T2 控制流', src: T2, expect: { ok: false, diagCodes: ['E205', 'E205', 'E205'], mainStmts: 6, maxDepth: 2, subs: 0 } },
  { name: 'T3-1 未闭合字符串', src: `let s = "abc`, expect: { ok: false, diagCodes: ['E102'] } },
  { name: 'T3-2 缺 end', src: `if x then`, expect: { ok: false, diagCodes: ['E105'] } },
  { name: 'T3-3 end 多余', src: `end if`, expect: { ok: false, diagCodes: ['E105'] } },
  { name: 'T3-4 表达式不完整', src: `click (800,600`, expect: { ok: false, diagCodes: ['E106'] } },
  { name: 'T3-5 call 未定义', src: `call 不存在()`, expect: { ok: false, diagCodes: ['E205'] } },
  { name: 'T3-6 break 越位', src: `break`, expect: { ok: false, diagCodes: ['E202'] } },
  { name: 'T3-7 重复定义', src: `sub A()\nend sub\nsub A()\nend sub`, expect: { ok: false, diagCodes: ['E204'] } },
  // —— P1b 语义层用例（依主审裁决修正码号）——
  { name: 'T3-8 call 未定义', src: `call foo`, expect: { ok: false, diagCodes: ['E205'] } },
  { name: 'T3-9 前向调用 OK', src: `sub a\nend\ncall a`, expect: { ok: true, diagCodes: [] } },
  { name: 'T3-10 break 越位(E202)', src: `if x\nbreak\nend`, expect: { ok: false, diagCodes: ['E202'] } },
  { name: 'T3-11 循环内 break OK', src: `for i = 1 to 3\nbreak\nend`, expect: { ok: true, diagCodes: [] } },
  { name: 'T3-12 空程序合法', src: ``, expect: { ok: true, diagCodes: [] } },
  { name: 'T3-13 case 空 E209', src: `switch a\ncase 1\nend`, expect: { ok: false, diagCodes: ['E209'] } },
];

function sortKeys(o) {
  if (Array.isArray(o)) return o.map(sortKeys);
  if (o && typeof o === 'object') { const r = {}; for (const k of Object.keys(o).sort()) r[k] = sortKeys(o[k]); return r; }
  return o;
}

let pass = 0, fail = 0;
for (const c of CASES) {
  const ast = parse(c.src);
  const gotCodes = ast.diagnostics.map((d) => d.code).sort();
  const expCodes = c.expect.diagCodes.slice().sort();
  let okFlag = ast.ok === c.expect.ok;
  let codeOk = JSON.stringify(sortKeys(gotCodes)) === JSON.stringify(sortKeys(expCodes));
  let structOk = true;
  if (c.expect.mainStmts != null) structOk = structOk && ast.main.stmts.length === c.expect.mainStmts;
  if (c.expect.maxDepth != null) structOk = structOk && ast.stats.maxDepth === c.expect.maxDepth;
  if (c.expect.subs != null) structOk = structOk && ast.subs.length === c.expect.subs;
  if (okFlag && codeOk && structOk) { pass++; }
  else {
    fail++;
    console.log('FAIL', c.name, '=> got', JSON.stringify({ ok: ast.ok, codes: gotCodes, main: ast.main.stmts.length, depth: ast.stats.maxDepth, subs: ast.subs.length }));
  }
}
console.log('PASS ' + pass + '/' + (pass + fail));
if (fail > 0) process.exitCode = 1;

// gui/test_roundtrip.js — P2: T5 结构断言（P3 再追加 T4 往返用例）
// 用法: node gui/test_roundtrip.js --t5
'use strict';
const fs = require('fs');
const path = require('path');
const { stripComments, mergeContinuedLines, tokenize, parseProgram } = require('./dsl_parser.js');
const { buildFlow } = require('./dsl_to_flow.js');
const { toDsl, normalize } = require('./flow_to_dsl.js');

// T5 结构断言（D-R1d 回填: 底稿 §6.5 初值 29/6/5 → 严格按 §5.2「emitIf 生成 :true/:false 两键」
// 且每子图强制含 start+end（§4.2 连线规则）后的实测值）
const T5_EXPECT = { nodes: 34, graphs: 8, keys: 7 };
const T5_DOC = path.join(__dirname, '..', 'docs', 'dsl-plan', 'APPENDIX_A_T5_DSL.md');

function loadT5() {
  const md = fs.readFileSync(T5_DOC, 'utf8');
  const i = md.indexOf('## 4.');
  const seg = i >= 0 ? md.slice(i) : md;
  const m = /```dsl\r?\n([\s\S]*?)```/.exec(seg);
  if (!m) throw new Error('APPENDIX_A_T5_DSL.md §4 未找到 ```dsl 代码块');
  return m[1];
}

function parse(src) {
  const { lines } = mergeContinuedLines(stripComments(src));
  const lexDiags = [];
  const ast = parseProgram(tokenize(lines, lexDiags));
  ast.diagnostics = lexDiags.concat(ast.diagnostics).slice(0, 10);
  ast.ok = ast.diagnostics.length === 0;
  return ast;
}

function runT5() {
  const ast = parse(loadT5());
  const flow = buildFlow(ast);
  const got = { nodes: flow.stats.nodes, graphs: flow.stats.graphs, keys: flow.stats.keys };
  const bad = [];
  ['nodes', 'graphs', 'keys'].forEach(function (k) {
    if (got[k] !== T5_EXPECT[k]) bad.push(k + ' 期望 ' + T5_EXPECT[k] + ' 实得 ' + got[k]);
  });
  if (ast.diagnostics.length) bad.push('parse diagnostics=' + JSON.stringify(ast.diagnostics.map((d) => d.code)));
  if (!/^[0-9a-f]{8}$/.test(String(flow.graphHash))) bad.push('graphHash 非 8 位十六进制: ' + flow.graphHash);
  if (bad.length) {
    console.log('T5 FAIL ' + bad.join(' | '));
    console.log('  keys: ' + JSON.stringify(flow.stats.keyList));
    console.log('  perGraph: ' + JSON.stringify(Object.keys(flow.subgraphs).reduce(function (o, k) {
      o[k] = flow.subgraphs[k].nodes.length; return o;
    }, { main: flow.graph.nodes.length })));
    process.exitCode = 1;
    return false;
  }
  console.log('T5 PASS nodes=' + got.nodes + ' graphs=' + got.graphs + ' keys=' + got.keys +
    ' edges=' + flow.stats.edges + ' graphHash=' + flow.graphHash);
  return true;
}

function dumpT5() {
  const ast = parse(loadT5());
  const flow = buildFlow(ast);
  console.log('nodes=' + flow.stats.nodes + ' edges=' + flow.stats.edges +
    ' graphs=' + flow.stats.graphs + ' keys=' + flow.stats.keys);
  console.log('keyList=' + JSON.stringify(flow.stats.keyList, null, 1));
  console.log('main: ' + flow.graph.nodes.map(function (n) { return n.type + (n.cfg.op ? '/' + n.cfg.op : ''); }).join(' → '));
  Object.keys(flow.subgraphs).forEach(function (k) {
    console.log(k + ': ' + flow.subgraphs[k].nodes.map(function (n) { return n.type + (n.cfg.op ? '/' + n.cfg.op : ''); }).join(' → '));
  });
  console.log('breakEdges=' + JSON.stringify(flow.edges.filter(function (e) { return e.label === 'break'; })));
  console.log('warnings=' + JSON.stringify(flow.warnings));
}

/* ================= T4 往返用例（5 组, 底稿 §6.4; ①T2 全文 ②T5 全文 ③main+3 简单 ④switch 四分支 ⑤双层嵌套 for+if+break） ================= */
const T4_CASES = [
  { name: 'T2_full', code:
`main
  Let hp = 100
  If hp < 30 Then
    Call 使用药水()
  ElseIf hp < 60 Then
    Delay 2000
  Else
    TracePrint "状态正常"
  End If
  For i = 1 To 10 Step 2
    Tap 500 + i * 10, 300
    Delay 200
  Next
  While exists image "loading.png" threshold 0.8
    Delay 500
  Wend
  Do
    TapImage "next_btn.png"
    Delay 1000
    If FindImage "done.png" threshold 0.9 Then
      Exit Do
    End If
  Loop
  Switch stage
    Case 1: Call 第一关()
    Case 2: Call 第二关()
    Default: TracePrint "未知关卡"
  End Switch
end main` },
  { name: 'T5_full', code:
`// 任务书 3.1 重构版（D-R1d 数据源）
main
  Call 启动游戏()
  Call 关闭弹窗()
  Do
    Call 刷一关()
    If FindImage "done.png" threshold 0.9 Then
      Exit Do
    End If
  Loop
  TracePrint "全部完成"
end main

Sub 启动游戏()
  TapImage "main_btn.png" threshold 0.85
  Delay 1000
  WaitImage "home.png" timeout 10s
End Sub

Sub 关闭弹窗()
  Do While FindImage "close.png" threshold 0.8
    Tap 960, 60
    Delay 500
  Loop
End Sub

Sub 刷一关()
  Tap 500, 800
  Delay 500
  WaitImage "result.png" timeout 120s
  Tap 960, 540
  Delay 1000
End Sub` },
  { name: 'main_3simple', code:
`main
  Let hp = 100
  Tap 500, 300
  TracePrint "hello"
end main` },
  { name: 'switch_4branch', code:
`main
  Switch mode
    Case 1: Tap 10, 20
    Case 2: Tap 30, 40
    Case 3: Tap 50, 60
    Case 4: Tap 70, 80
    Default: TracePrint "other"
  End Switch
end main` },
  { name: 'nested_for_if_break', code:
`main
  For i = 1 To 3
    For j = 1 To 3
      If j == 2 Then
        Exit For
      End If
    Next
  Next
end main` }
];

// astEqual: 深度比较, 忽略 line/col/diagnostics/stats (底稿 §6.4)
function astEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false;
  var skip = { line: 1, col: 1, diagnostics: 1, stats: 1 };
  var ka = Object.keys(a).filter(function (k) { return !skip[k]; }).sort();
  var kb = Object.keys(b).filter(function (k) { return !skip[k]; }).sort();
  if (ka.join(',') !== kb.join(',')) return false;
  for (var i = 0; i < ka.length; i++) if (!astEqual(a[ka[i]], b[ka[i]])) return false;
  return true;
}

// T4 往返: toDsl(buildFlow(parse(src))) 与 normalize(src) 比对; 文本不等走 AST 结构等价兜底
function runT4() {
  var pass = 0;
  for (var i = 0; i < T4_CASES.length; i++) {
    var c = T4_CASES[i];
    var src = parse(c.code);
    var flow = buildFlow(src);
    var rebuilt = toDsl(flow);
    var textOk = normalize(rebuilt) === normalize(c.code);
    var astOk = false;
    if (!textOk) {
      try {
        var rt = parse(rebuilt);
        astOk = astEqual(src.main, rt.main) && astEqual(src.subs, rt.subs);
      } catch (e) { astOk = false; }
    }
    if (textOk || astOk) { pass++; console.log('T4 ' + c.name + ' PASS (' + (textOk ? 'text' : 'ast') + ')'); }
    else console.log('T4 ' + c.name + ' FAIL');
  }
  console.log('T4 PASS ' + pass + '/' + T4_CASES.length);
  return pass === T4_CASES.length;
}

const argv = process.argv.slice(2);
if (argv.indexOf('--dump') >= 0) dumpT5();
else if (argv.indexOf('--t4') >= 0) { const ok = runT4(); if (!ok) process.exitCode = 1; }
else if (argv.indexOf('--t5') >= 0) runT5();
else { const ok5 = runT5(); const ok4 = runT4(); if (!ok4 || !ok5) process.exitCode = 1; }

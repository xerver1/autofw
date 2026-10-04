// 循环迭代等待延时 delay 子句: 解析/序列化/往返 单元测试（非 UX 红线脚本，纯逻辑验证）
const P = require("./dsl_parser.js");
const TF = require("./dsl_to_flow.js");
const FD = require("./flow_to_dsl.js");

// 正确解析管线: stripComments → mergeContinuedLines → tokenize → parseProgram
function parseDsl(src) {
  const { lines } = P.mergeContinuedLines(P.stripComments(src));
  return P.parseProgram(P.tokenize(lines, []));
}

let fails = 0;
function ok(name, cond, extra) {
  if (cond) console.log("  PASS " + name);
  else { console.log("  FAIL " + name + (extra ? "  :: " + extra : "")); fails++; }
}

const dsl = `main
  For i = 1 To 5 delay 2000
    adb_tap 100 200
  Next
  Do While image "b.png" delay 500
    adb_tap 100 200
  Loop
  While text "c" delay 3000
    adb_tap 100 200
  Wend
end main`;

const ast = parseDsl(dsl);
const flow = TF.buildFlow(ast);
const nodes = flow.graph ? flow.graph.nodes : flow.nodes;
const byType = {};
nodes.forEach(n => { byType[n.type] = byType[n.type] || []; byType[n.type].push(n); });

ok("for 节点含 delay_ms=2000", byType.loop_for && byType.loop_for[0].config.delay_ms === 2000,
   byType.loop_for && JSON.stringify(byType.loop_for[0].config));
ok("while(do) 节点含 delay_ms=500", byType.loop_while && byType.loop_while.some(n => n.config.delay_ms === 500),
   byType.loop_while && JSON.stringify(byType.loop_while.map(n => n.config.delay_ms)));
ok("while 节点含 delay_ms=3000", byType.loop_while && byType.loop_while.some(n => n.config.delay_ms === 3000),
   byType.loop_while && JSON.stringify(byType.loop_while.map(n => n.config.delay_ms)));

const ds2 = FD.toDsl(flow);
console.log("--- 序列化输出(含 delay 子句) ---");
ds2.split("\n").filter(l => /delay|While|For|Do /.test(l)).forEach(l => console.log("  " + l));
ok("序列化含 'delay 2000'", ds2.indexOf("delay 2000") >= 0);
ok("序列化含 'delay 500'", ds2.indexOf("delay 500") >= 0);
ok("序列化含 'delay 3000'", ds2.indexOf("delay 3000") >= 0);

// 往返: 再解析 + 再构建, 确认 delay 稳定
const flow2 = TF.buildFlow(parseDsl(ds2));
const nodes2 = flow2.graph ? flow2.graph.nodes : flow2.nodes;
const byType2 = {};
nodes2.forEach(n => { byType2[n.type] = byType2[n.type] || []; byType2[n.type].push(n); });
ok("往返后 for delay_ms=2000", byType2.loop_for && byType2.loop_for[0].config.delay_ms === 2000);
ok("往返后 while 含 500/3000", byType2.loop_while &&
   byType2.loop_while.some(n => n.config.delay_ms === 500) &&
   byType2.loop_while.some(n => n.config.delay_ms === 3000));

// 反向兼容: 无 delay 时不输出 delay 子句
const dslNo = `main
  While image "a.png"
    adb_tap 100 200
  Wend
end main`;
const dsNo = FD.toDsl(TF.buildFlow(parseDsl(dslNo)));
ok("无 delay 时不输出 'delay' 子句", dsNo.indexOf("delay") < 0, dsNo.split("\n").find(l => l.indexOf("While") >= 0));

console.log(fails === 0 ? "\nALL PASS" : "\n" + fails + " FAILED");
process.exit(fails === 0 ? 0 : 1);

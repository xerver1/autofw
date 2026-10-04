// gui/test_userwalk.js — 用户视角功能遍历测试（易用性/逻辑性/延续性）
// 用法: node gui/test_userwalk.js
// 覆盖: 写 DSL → parseProgram → buildFlow → toDsl(往返) → buildMd(导出)
'use strict';
const { stripComments, mergeContinuedLines, tokenize, parseProgram } = require('./dsl_parser.js');
const { buildFlow, graphHash, canonical } = require('./dsl_to_flow.js');
const { toDsl, normalize } = require('./flow_to_dsl.js');
const { buildMd } = require('./export_md.js');

// 引擎已知 NODE_TYPES（P0-A1 铁证）
const KNOWN = ['start', 'end', 'action', 'condition', 'if_else', 'switch_case',
  'loop_for', 'loop_while', 'loop_do_while', 'print', 'sub_call'];

function parse(src) {
  const { lines } = mergeContinuedLines(stripComments(src));
  const lexDiags = [];
  const ast = parseProgram(tokenize(lines, lexDiags));
  ast.diagnostics = (lexDiags.concat(ast.diagnostics || [])).slice(0, 10);
  ast.ok = ast.diagnostics.length === 0;
  return ast;
}
function allTypesKnown(flow) {
  const bad = [];
  const g = flow.graph; if (g) g.nodes.forEach(n => { if (KNOWN.indexOf(n.type) < 0) bad.push('graph.' + n.type); });
  Object.keys(flow.subgraphs || {}).forEach(k => flow.subgraphs[k].nodes.forEach(n => {
    if (KNOWN.indexOf(n.type) < 0) bad.push(k + '.' + n.type);
  }));
  flow.edges.forEach(e => { if (!e.id || !e.from || !e.to) bad.push('edge-missing-field'); });
  return bad;
}
function hashOf(src) { return buildFlow(parse(src)).graphHash; } // 同语义: 内部按 {graph,subgraphs} 哈希, 验证同输入重建确定性

// ---------- 用户场景 ----------
const SC = [
  { name: 'S1 新手:主流程点击+延时+日志', dsl:
`main
  click (500, 300)
  delay 1s
  log "启动完成"
end main` },
  { name: 'S2 子程序:模块化启动', dsl:
`main
  call 启动游戏()
end main
sub 启动游戏()
  click image "main_btn.png" threshold 0.85
  delay 2s
end sub` },
  { name: 'S3 分支:if/else if/else', dsl:
`main
  if hp < 50 then
    log "低血"
  else if hp < 80 then
    log "中血"
  else
    log "高血"
  end if
end main` },
  { name: 'S4 循环:for + break', dsl:
`main
  for i = 1 to 10
    if found then break end
    click (100, 100)
  end for
end main` },
  { name: 'S5 循环:while + find 条件', dsl:
`main
  while exists image "loading.png" threshold 0.8
    delay 500ms
  end while
end main` },
  { name: 'S6 开关:switch/case/default', dsl:
`main
  switch mode
    case 1: click (10, 20)
    case 2: click (30, 40)
    default: log "other"
  end switch
end main` },
  { name: 'S7 空主流程', dsl:
`main
end main` },
  { name: 'S8 注释+中文+续行', dsl:
`// 注释测试
main
  click (500, 300)  // 点击坐标
  log "中文日志"
  let step = 1
end main` },
  { name: 'S9 continue 关键字(规格外, 验证被正确拒绝)', dsl:
`main
  for i = 1 to 3
    for j = 1 to 3
      if j == 2 then
        continue
      end if
    end for
  end for
end main`, expectErr: ['E107'] },
  { name: 'S10 错误处理:break 越位', dsl:
`main
  break
end main`, expectErr: ['E202'] },
  { name: 'S11 错误处理:调用未定义子程序', dsl:
`main
  call 不存在()
end main`, expectErr: ['E205'] },
  { name: 'S12 错误处理:缺少 end', dsl:
`main
  click (1, 1)
`, expectErr: ['E105'] },
  { name: 'S13 嵌套循环(合法, 无continue)', dsl:
`main
  for i = 1 to 2
    for j = 1 to 2
      click (i * 10, j * 10)
    end for
  end for
end main` },
];

let pass = 0, fail = 0;
const findings = [];
console.log('=== 用户场景功能遍历（逻辑层）===\n');
SC.forEach(function (s) {
  let line = '[' + s.name + '] ';
  try {
    const ast = parse(s.dsl);
    const flow = buildFlow(ast);
    const badTypes = allTypesKnown(flow);
    const h1 = flow.graphHash, h2 = hashOf(s.dsl); // 同进程二次构建
    const hashStable = (h1 === h2);
    const md = buildMd(flow, { taskName: s.name, device: 'emulator-5554', note: 'walktest' });
    const mdOk = ['## 执行参数', '## 变量表', '## 主流程', '## 子流程', '## 备注'].every(h => md.indexOf(h) >= 0);

    if (s.expectErr) {
      // 错误场景: 期望有诊断且含预期码
      const codes = (ast.diagnostics || []).map(d => d.code);
      const hit = s.expectErr.every(c => codes.indexOf(c) >= 0);
      const ok = ast.diagnostics.length > 0 && hit && badTypes.length === 0 && hashStable;
      line += (ok ? 'PASS' : 'FAIL') + ' 诊断码=' + JSON.stringify(codes) +
        ' 信息=' + JSON.stringify((ast.diagnostics || []).map(d => d.message).slice(0, 2)) +
        ' 节点类型已知=' + (badTypes.length === 0) + ' 哈希稳定=' + hashStable;
      if (!ok) { fail++; findings.push('错误场景 ' + s.name + ' 未达预期: 诊断=' + JSON.stringify(codes)); }
      else pass++;
    } else {
      const ok = ast.ok && badTypes.length === 0 && hashStable && mdOk;
      line += (ok ? 'PASS' : 'FAIL') +
        ' nodes=' + flow.stats.nodes + ' graphs=' + flow.stats.graphs + ' keys=' + flow.stats.keys +
        ' edges=' + flow.stats.edges + ' hash=' + h1 +
        ' 类型已知=' + (badTypes.length === 0) + (badTypes.length ? '(' + badTypes.join(',') + ')' : '') +
        ' 哈希稳定=' + hashStable + ' 导出MD六段=' + mdOk;
      if (!ok) { fail++; if (!ast.ok) findings.push('解析报错(疑似功能缺陷): ' + s.name + ' 诊断=' + JSON.stringify((ast.diagnostics || []).map(d => d.code))); if (badTypes.length) findings.push('未知节点类型: ' + s.name + ' ' + badTypes.join(',')); if (!hashStable) findings.push('哈希不可复现: ' + s.name); if (!mdOk) findings.push('导出MD缺段: ' + s.name); }
      else pass++;
    }
    // 导出保真度抽检: call 节点是否保留子程序名
    if (s.name.indexOf('S2') === 0) {
      const allNodes = flow.graph.nodes.concat(Object.keys(flow.subgraphs).reduce((a, k) => a.concat(flow.subgraphs[k].nodes), []));
      const callNode = allNodes.find(n => (n.cfg || {}).op === 'call');
      const subName = callNode ? callNode.cfg.sub : null;
      // 往返 DSL 保真(toDsl 路径)
      const rtDsl = callNode ? toDsl({ graph: flow.graph, subgraphs: flow.subgraphs }) : '';
      const rtKeeps = subName ? rtDsl.indexOf(subName) >= 0 : true;
      // 导出 MD 保真(buildMd 路径, 即 export_md.js nodeToDsl) — 仅查调用点(主流程), 排除子流程标题里的子程序名
      const md2 = buildMd(flow, { taskName: s.name });
      const mainSec = md2.split('## 主流程')[1] ? md2.split('## 主流程')[1].split('## 子流程')[0] : '';
      const mdKeeps = subName ? mainSec.indexOf(subName) >= 0 : true;
      line += ' | call往返保真=' + rtKeeps + ' call导出MD保真=' + mdKeeps + (subName ? '(sub=' + subName + ')' : '');
      if (!rtKeeps) findings.push('往返缺陷: call 节点丢失子程序名(sub=' + subName + ')');
      if (!mdKeeps) findings.push('导出MD缺陷: call 节点丢失子程序名(sub=' + subName + '), 导出文本仅 "call" (export_md.js nodeToDsl 无 op==="call" 分支)');
    }
    console.log(line);
  } catch (e) {
    fail++;
    console.log(line + ' CRASH ' + (e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e));
    findings.push('CRASH ' + s.name + ': ' + e);
  }
});

// ---------- 延续性: 往返结构等价 ----------
console.log('\n=== 延续性: DSL→图→DSL→图 往返 ===');
['S2 子程序:模块化启动', 'S6 开关:switch/case/default', 'S13 嵌套循环(合法, 无continue)'].forEach(function (nm) {
  const s = SC.find(x => x.name === nm);
  try {
    const f1 = buildFlow(parse(s.dsl));
    const dsl2 = toDsl({ graph: f1.graph, subgraphs: f1.subgraphs });
    const f2 = buildFlow(parse(dsl2));
    const stable = f1.stats.nodes === f2.stats.nodes && f1.stats.keys === f2.stats.keys &&
      f1.stats.graphs === f2.stats.graphs && f1.graphHash === f2.graphHash;
    console.log('[' + nm + '] 往返 stats 稳定=' + stable +
      ' (' + f1.stats.nodes + '/' + f1.stats.keys + '/' + f1.stats.graphs + ' → ' + f2.stats.nodes + '/' + f2.stats.keys + '/' + f2.stats.graphs + ')');
    if (!stable) findings.push('往返不一致: ' + nm);
  } catch (e) {
    fail++;
    console.log('[' + nm + '] 往返 CRASH ' + e);
    findings.push('往返CRASH ' + nm);
  }
});

console.log('\n=== 汇总 ===');
console.log('场景 PASS=' + pass + ' FAIL=' + fail);
console.log('发现项(' + findings.length + '):');
findings.forEach(f => console.log('  - ' + f));
process.exitCode = fail > 0 ? 1 : 0;

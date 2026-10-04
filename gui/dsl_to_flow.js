// gui/dsl_to_flow.js — DSL AST → 预览图 cfg; 纯函数 + UMD 导出（无 DOM）
// 规格: WO-P2 §1–§4 / 底稿 §4.2 节点映射表 / §5.2 原子清单 / 附录 A2 布局常量
// 硬约束: node.type ∈ NODE_TYPES(start,end,action,condition,if_else,switch_case,
//         loop_for,loop_while,loop_do_while,print)  —— P0-A1 铁证, 未知 type 会被 validate 判 error
'use strict';

/* ================= 常量 ================= */
var LAY = { X0: 80, DX: 190, Y0: 60, DY: 110, FALSE_SHIFT: 190 };
// canonical 剔除的非结构字段（裁决: x/y/dslLine/updatedAt；pos 为 x/y 镜像, graphHash 为自身产物）
var HASH_SKIP = { x: 1, y: 1, pos: 1, dslLine: 1, updatedAt: 1, graphHash: 1 };
// 简单原语 → action.cfg.op（底稿 §4.2）
var OP_SIMPLE = {
  swipe: 'swipe', drag: 'swipe', long_press: 'swipe', pinch: 'pinch',
  key: 'key', key_hold: 'key', type: 'input_text', screenshot: 'screenshot',
  app_start: 'am_start', app_stop: 'am_stop', app_current: 'app_current',
  emulator: 'emulator', read: 'read_file', file: 'read_file',
  find: 'find', find_all: 'find', exists: 'find', count: 'find',
  ocr: 'ocr', ocr_exists: 'ocr', ocr_number: 'ocr_number', color_at: 'color_at',
  color: 'find_color', colors: 'find_color', text: 'ocr'
};

/* ================= 基础工具 ================= */
var _seq = 0;
// 注意: id 必须确定（仅用序号, 不含 Date.now/Math.random）, 否则 canonical/graphHash 会因随机 id 不可复现,
// 破坏 D10 同步状态机（依赖稳定 graphHash 判代码↔图变更）。buildFlow 入口会重置 _seq。
function mkId(p) { _seq += 1; return p + '_' + _seq; }
function tv(tokens) { return (tokens || []).map(function (t) { return t && t.v; }); }
function textOf(tokens) { return tv(tokens).join(' '); }
function unq(s) { return String(s == null ? '' : s).replace(/^"/, '').replace(/"$/, ''); }
function firstStr(tokens) {
  var a = tokens || [];
  for (var i = 0; i < a.length; i++) if (a[i] && a[i].t === 'STR') return unq(a[i].v);
  return null;
}
function firstHex(tokens) {
  var a = tokens || [];
  for (var i = 0; i < a.length; i++) if (a[i] && a[i].t === 'HEX') return a[i].v;
  return null;
}
function valAfter(tokens, word) {
  var a = tokens || []; var w = String(word).toLowerCase();
  for (var i = 0; i < a.length - 1; i++) if (a[i] && a[i].v.toLowerCase() === w) return a[i + 1].v;
  return null;
}
function numAfter(tokens, word) {
  var v = valAfter(tokens, word);
  if (v == null) return null;
  var n = parseFloat(v);
  return isNaN(n) ? null : n;
}
// "1s" / "500ms" / "1s~2s" → 毫秒（区间取下界, 上界另存）
function msOf(v) {
  if (v == null) return null;
  var m = /^(\d+(?:\.\d+)?)(ms|s)/.exec(String(v));
  if (!m) { var n = parseFloat(v); return isNaN(n) ? null : n; }
  return m[2] === 's' ? Math.round(parseFloat(m[1]) * 1000) : Math.round(parseFloat(m[1]));
}
function msMaxOf(v) {
  var m = /~(\d+(?:\.\d+)?)(ms|s)/.exec(String(v == null ? '' : v));
  return m ? (m[2] === 's' ? Math.round(parseFloat(m[1]) * 1000) : Math.round(parseFloat(m[1]))) : null;
}
function firstTime(tokens) {
  var a = tokens || [];
  for (var i = 0; i < a.length; i++) if (a[i] && a[i].t === 'TIME') return a[i].v;
  for (var j = 0; j < a.length; j++) if (a[j] && a[j].t === 'NUM') return a[j].v;
  return null;
}
// 括号内的数字序列（click (960, 60) → [960,60]）
function parenNums(tokens) {
  var a = tokens || [], out = [], depth = 0;
  for (var i = 0; i < a.length; i++) {
    var t = a[i];
    if (!t) continue;
    if (t.t === 'OP' && t.v === '(') { depth++; continue; }
    if (t.t === 'OP' && t.v === ')') { depth--; continue; }
    if (depth > 0 && t.t === 'NUM') out.push(parseFloat(t.v));
  }
  if (!out.length) for (var k = 0; k < a.length; k++) if (a[k] && a[k].t === 'NUM') out.push(parseFloat(a[k].v));
  return out;
}

/* ================= canonical / graphHash（WO §4） ================= */
function norm(v) {
  if (Array.isArray(v)) return v.map(norm);
  if (v && typeof v === 'object') {
    var out = {}, ks = Object.keys(v).filter(function (k) { return !HASH_SKIP[k]; }).sort();
    for (var i = 0; i < ks.length; i++) out[ks[i]] = norm(v[ks[i]]);
    return out;
  }
  return v;
}
function canonical(v) { return JSON.stringify(norm(v)); }
function graphHash(g) {                       // FNV-1a 32bit → 十六进制 8 位
  var s = canonical(g), h = 0x811c9dc5;
  for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return ('0000000' + h.toString(16)).slice(-8);
}

/* ================= 节点 / 边 / 子图 ================= */
function graphOf(ctx, key, depth) {
  if (!ctx.subgraphs[key]) {
    ctx.subgraphs[key] = { nodes: [], edges: [] };
    ctx.depth[key] = depth || 0;
    ctx.order.push(key);
  }
  return ctx.subgraphs[key];
}
// mkNode(type, cfg, dslLine): uid 前缀 n_; 返回值含 dslLine（P5 运行高亮依赖）
// cfg 与 config 指向同一对象: cfg=WO/§5.2 命名, config=引擎/编辑器命名
function mkNode(ctx, key, type, name, cfg, dslLine, forceId) {
  var c = cfg || {};
  var n = {
    id: forceId || mkId('n'), type: type, name: name || type,
    x: 0, y: 0, pos: { x: 0, y: 0 },
    cfg: c, config: c, dslLine: dslLine == null ? null : dslLine
  };
  ctx.nodes.push(n);
  graphOf(ctx, key).nodes.push(n);
  return n;
}
function mkEdge(ctx, key, from, to, label) {   // uid 前缀 e_
  var e = { id: mkId('e'), from: from, to: to, label: label || '' };
  ctx.edges.push(e);
  graphOf(ctx, key).edges.push(e);
  return e;
}
// link(prevExit[], nextEntry): 批量连边; exit 元素可为 id 字符串或 {id,label}
function link(ctx, key, prevExit, nextEntry, label) {
  if (!nextEntry) return;
  var a = prevExit || [];
  for (var i = 0; i < a.length; i++) {
    var x = a[i];
    if (!x) continue;
    if (typeof x === 'string') mkEdge(ctx, key, x, nextEntry, label);
    else mkEdge(ctx, key, x.id, nextEntry, x.label || label);
  }
}

/* ================= 条件 → cfg（引擎 kind: match_image/ocr_contains/expr, F-A3） ================= */
function condCfg(cond) {
  var toks = (cond && cond.tokens) || [], vals = tv(toks), lvals = vals.map(function (v) { return String(v).toLowerCase(); }), raw = textOf(toks);
  var img = firstStr(toks), th = numAfter(toks, 'threshold');
  if (lvals.indexOf('image') >= 0 && img) return { kind: 'match_image', image: img, threshold: th == null ? 0.85 : th, expr: raw };
  if ((lvals.indexOf('text') >= 0 || lvals.indexOf('ocr') >= 0) && img) return { kind: 'ocr_contains', text: img, expr: raw };
  return { kind: 'expr', expr: raw };
}

/* ================= 原语 → 节点（底稿 §4.2；兼容按键精灵别名） ================= */
function mapPrim(st) {
  var a = st.args || [], name = (st.name || '').toLowerCase(), vals = tv(a);
  var has = function (w) { return vals.indexOf(w) >= 0; };
  var img = firstStr(a), th = numAfter(a, 'threshold'), nums = parenNums(a), raw = textOf(a);
  var t0 = firstTime(a);
  // —— 按键精灵别名归一（命令式动词 → 内部原语名）——
  if (name === 'tap') name = 'click';
  else if (name === 'tapimage') name = 'click';          // TapImage "图"：无 image 关键字, 走下方 firstStr 兜底
  else if (name === 'doubletap') name = 'double_click';
  else if (name === 'longtap') name = 'long_press';
  else if (name === 'keypress') name = 'key';
  else if (name === 'saystring') return { type: 'action', name: '输入文本', cfg: { op: 'input_text', text: img, args: raw } };
  else if (name === 'snapshot') name = 'screenshot';
  else if (name === 'findimage') name = 'find';
  else if (name === 'waitimage') name = 'wait';
  else if (name === 'waitgone') name = 'wait_gone';
  else if (name === 'waittext') return { type: 'action', name: '等待文字', cfg: { op: 'wait_text', image: img, threshold: th, timeout_ms: msOf(valAfter(a, 'timeout')) || 0, args: raw } };
  else if (name === 'waitkey') return { type: 'action', name: '等待按键', cfg: { op: 'wait_key', key: img, args: raw } };
  else if (name === 'appstart') name = 'app_start';
  else if (name === 'appstop') name = 'app_stop';
  else if (name === 'traceprint' || name === 'warn') name = 'log';
  if (name === 'click' || name === 'double_click') {
    var times = name === 'double_click' ? 2 : 1;
    if (has('image') || name === 'click' && img && nums.length < 2) {
      var useImg = img || firstStr(a);
      return { type: 'action', name: '点击图像', cfg: { op: 'tap_image', image: useImg, threshold: th, times: times, args: raw } };
    }
    if (nums.length >= 2) return { type: 'action', name: '点击', cfg: { op: 'tap', x: nums[0], y: nums[1], times: times, args: raw } };
    var fim = firstStr(a);
    if (fim) return { type: 'action', name: '点击图像', cfg: { op: 'tap_image', image: fim, threshold: th, times: times, args: raw } };
    return { type: 'action', name: '点击', cfg: { op: 'tap', x: null, y: null, times: times, args: raw } };
  }
  if (name === 'delay') {
    return { type: 'action', name: '延时', cfg: { op: 'delay', ms: msOf(t0), ms_max: msMaxOf(t0), random: msMaxOf(t0) != null, args: raw } };
  }
  if (name === 'wait' || name === 'wait_gone') {
    var op = name === 'wait_gone' ? 'wait_gone' : (has('image') ? 'wait_image' : (has('text') ? 'wait_text' : (has('key') ? 'wait_key' : 'wait_image')));
    return {
      type: 'action', name: name === 'wait_gone' ? '等待消失' : '等待出现',
      cfg: { op: op, image: img, threshold: th, timeout_ms: msOf(valAfter(a, 'timeout') || t0), args: raw }
    };
  }
  var simple = OP_SIMPLE[name];
  if (simple) return { type: 'action', name: name, cfg: { op: simple, image: img, color: firstHex(a), args: raw } };
  return { type: 'action', name: name, cfg: { op: name, args: raw } };   // 兜底: op 承载原语名
}

/* ================= 子图生成 ================= */
// 每个子图强制含 start+end（引擎坑: 缺 start 报「循环体缺少开始节点」, 底稿 §4.2 连线规则）
function emitGraph(ctx, key, stmts, depth, hook) {
  graphOf(ctx, key, depth);
  var body = stmts || [];
  var start = mkNode(ctx, key, 'start', '开始', {}, body.length ? body[0].line : null);
  var endId = mkId('n');
  ctx.graphEnd[key] = endId;
  if (hook) hook(start.id, endId);
  var r = emitStmts(body, ctx, key, depth);
  mkNode(ctx, key, 'end', '结束', {}, null, endId);
  if (r.entry) { link(ctx, key, [start.id], r.entry); link(ctx, key, r.exit, endId); }
  else link(ctx, key, [start.id], endId);
  return { startId: start.id, endId: endId };
}
// emitStmts → {entry, exit[]}（exit 为数组: 分支汇合语义）
function emitStmts(stmts, ctx, key, depth) {
  var entry = null, exit = [], a = stmts || [];
  for (var i = 0; i < a.length; i++) {
    var r = emitStmt(a[i], ctx, key, depth);
    if (!r || !r.entry) continue;
    if (!entry) entry = r.entry; else link(ctx, key, exit, r.entry);
    exit = r.exit || [];
  }
  return { entry: entry, exit: exit };
}
function one(ctx, key, type, name, cfg, line) {
  var n = mkNode(ctx, key, type, name, cfg, line);
  return { entry: n.id, exit: [n.id] };
}
function emitStmt(st, ctx, key, depth) {
  if (!st) return null;
  switch (st.kind) {
    case 'if': return emitIf(st, ctx, key, depth);
    case 'for': return emitFor(st, ctx, key, depth);
    case 'while': return emitWhile(st, ctx, key, depth);
    case 'loop': return emitLoop(st, ctx, key, depth);
    case 'do': return emitDo(st, ctx, key, depth);
    case 'switch': return emitSwitch(st, ctx, key, depth);
    case 'break': return emitBreak(st, ctx, key);
    case 'continue': return emitContinue(st, ctx, key);
    case 'let': return one(ctx, key, 'action', '赋值 ' + st.name, { op: 'assign', var: st.name, expr: textOf(st.value) }, st.line);
    case 'push': return one(ctx, key, 'action', '数组追加', { op: 'arr_push', expr: '' }, st.line);
    case 'call': return one(ctx, key, 'action', '调用 ' + st.sub, { op: 'call', sub: st.sub, args: textOf(st.args) }, st.line);
    case 'log': return one(ctx, key, 'action', st.level === 'warn' ? '警告' : '日志', { op: 'log', level: st.level, text: textOf(st.text) }, st.line);
    case 'return': case 'exit': return one(ctx, key, 'action', '结束脚本', { op: 'exit_script' }, st.line);
    case 'prim': var m = mapPrim(st); return one(ctx, key, m.type, m.name, m.cfg, st.line);
    case 'case': case 'default': return null;      // 由 emitSwitch 消费
    default: return one(ctx, key, 'action', String(st.kind || 'unknown'), { op: String(st.kind || 'unknown') }, st.line);
  }
}
// else-if 链 → false 子图内嵌套 if_else（§5.2 原子清单: 不是平铺）
function elseChain(st) {
  var eis = st.elseIfs || [];
  if (eis.length) {
    var h = eis[0];
    var ln = (h.cond && h.cond.tokens && h.cond.tokens[0] && h.cond.tokens[0].line) || st.line;
    return [{ kind: 'if', line: ln, cond: h.cond, then: h.body, elseIfs: eis.slice(1), elseBody: st.elseBody }];
  }
  return (st.elseBody && st.elseBody.stmts) || [];
}
// emitIf: <id>:true 与 <id>:false 两键都必须非空（缺 else 时 false 键含 start→end 直连）
function emitIf(st, ctx, key, depth) {
  var n = mkNode(ctx, key, 'if_else', '条件分支', condCfg(st.cond), st.line);
  emitGraph(ctx, n.id + ':true', (st.then && st.then.stmts) || [], depth + 1);
  emitGraph(ctx, n.id + ':false', elseChain(st), depth + 1);
  n.cfg.branch_true = n.id + ':true';
  n.cfg.branch_false = n.id + ':false';
  return { entry: n.id, exit: [{ id: n.id, label: 'true' }, { id: n.id, label: 'false' }] };
}
// 循环 body 子图键 = 循环节点 id（编辑器 enterCtx/delNode 同键约定）
function emitLoopBody(st, ctx, node, depth) {
  var rec = { loopId: ++ctx.loopSeq, key: node.id, startId: null, endId: null };
  ctx.loopStack.push(rec);
  ctx.loopMap[rec.loopId] = rec;
  emitGraph(ctx, node.id, (st.body && st.body.stmts) || [], depth + 1, function (sId, eId) {
    rec.startId = sId; rec.endId = eId;
  });
  ctx.loopStack.pop();
  node.cfg.body = node.id;
}
function emitFor(st, ctx, key, depth) {
  var cfg = {
    op: 'for', var: st.var, from: textOf(st.from && st.from.tokens), to: textOf(st.to && st.to.tokens),
    step: st.step ? textOf(st.step.tokens) : null
  };
  if (st.delay != null) cfg.delay_ms = st.delay;   // 循环迭代等待延时(代码行 `delay <ms>` 子句读回)
  var n = mkNode(ctx, key, 'loop_for', '循环 for', cfg, st.line);
  emitLoopBody(st, ctx, n, depth);
  return { entry: n.id, exit: [n.id] };
}
  function emitWhile(st, ctx, key, depth) {
    var cfg = condCfg(st.cond);
    cfg.op = 'while'; cfg.invert = (st.invert === true);   // Do Until / While not → invert=true(条件满足即退出)
    if (st.delay != null) cfg.delay_ms = st.delay;   // 循环迭代等待延时(代码行 `delay <ms>` 子句读回)
    var n = mkNode(ctx, key, 'loop_while', '循环 while', cfg, st.line);
    emitLoopBody(st, ctx, n, depth);
    return { entry: n.id, exit: [n.id] };
  }
function emitLoop(st, ctx, key, depth) {
  var n = mkNode(ctx, key, 'loop_do_while', '循环 loop', { op: 'loop', condition: true, invert: false }, st.line);
  emitLoopBody(st, ctx, n, depth);
  return { entry: n.id, exit: [n.id] };
}
// Do [While <cond> | Until <cond>] ... Loop  (按键精灵式; while 条件满足循环, until 条件满足退出)
  function emitDo(st, ctx, key, depth) {
    if (st.doKind === 'while') {
    var cw = condCfg(st.cond); cw.op = 'while'; cw.invert = false;
    if (st.delay != null) cw.delay_ms = st.delay;
    var nw = mkNode(ctx, key, 'loop_while', '循环 while', cw, st.line);
    emitLoopBody(st, ctx, nw, depth);
    return { entry: nw.id, exit: [nw.id] };
  }
  if (st.doKind === 'until') {
    var cu = condCfg(st.cond); cu.op = 'while'; cu.invert = true;   // invert=true: 条件满足即退出(=until)
    if (st.delay != null) cu.delay_ms = st.delay;
    var nu = mkNode(ctx, key, 'loop_while', '循环 while', cu, st.line);
    emitLoopBody(st, ctx, nu, depth);
    return { entry: nu.id, exit: [nu.id] };
  }
  var n = mkNode(ctx, key, 'loop_do_while', '循环 loop', { op: 'loop', condition: true, invert: false }, st.line);
  if (st.delay != null) n.config.delay_ms = st.delay;
  emitLoopBody(st, ctx, n, depth);
  return { entry: n.id, exit: [n.id] };
}
// D-R1c: break 出边指向「所在循环 body 子图的 end 节点」, label=break; 消费 AST 的 loopId
function resolveLoop(ctx, loopId) {
  if (loopId != null) {
    var rec = ctx.loopMap[loopId];
    if (rec && ctx.loopStack.indexOf(rec) >= 0) return rec;
  }
  return ctx.loopStack.length ? ctx.loopStack[ctx.loopStack.length - 1] : null;
}
function emitBreak(st, ctx, key) {
  var n = mkNode(ctx, key, 'action', '跳出循环', { op: 'break', loopId: st.loopId == null ? null : st.loopId }, st.line);
  var rec = resolveLoop(ctx, st.loopId);
  if (rec && rec.endId) mkEdge(ctx, key, n.id, rec.endId, 'break');
  else ctx.warnings.push({ code: 'W301', line: st.line, message: 'break 无所属循环, 未生成 break 边' });
  return { entry: n.id, exit: [] };              // break 后无顺序出边
}
function emitContinue(st, ctx, key) {
  var n = mkNode(ctx, key, 'action', '继续循环', { op: 'continue', loopId: st.loopId == null ? null : st.loopId }, st.line);
  var rec = resolveLoop(ctx, st.loopId);
  if (rec && rec.startId) mkEdge(ctx, key, n.id, rec.startId, 'continue');
  else ctx.warnings.push({ code: 'W302', line: st.line, message: 'continue 无所属循环, 未生成 continue 边' });
  return { entry: n.id, exit: [] };
}
// emitSwitch: 每 case 一键 <id>:case<N> + <id>:default（缺 default → 空图 + warning）
function emitSwitch(st, ctx, key, depth) {
  var cfg = { op: 'switch', expr: textOf(st.cond && st.cond.tokens), cases: [], default: null };
  var n = mkNode(ctx, key, 'switch_case', '多分支', cfg, st.line);
  var body = (st.body && st.body.stmts) || [], exits = [], idx = 0, hasDefault = false;
  for (var i = 0; i < body.length; i++) {
    var c = body[i];
    if (!c) continue;
    if (c.kind === 'case') {
      idx += 1;
      var ck = n.id + ':case' + idx;
      emitGraph(ctx, ck, c.stmt ? [c.stmt] : [], depth + 1);
      cfg.cases.push({ value: textOf(c.label && c.label.tokens), key: ck });
      exits.push({ id: n.id, label: 'case' + idx });
    } else if (c.kind === 'default') {
      hasDefault = true;
      var dk = n.id + ':default';
      emitGraph(ctx, dk, c.stmt ? [c.stmt] : [], depth + 1);
      cfg.default = dk;
      exits.push({ id: n.id, label: 'default' });
    } else {
      ctx.warnings.push({ code: 'W303', line: c.line, message: 'switch 体内非 case/default 语句已忽略' });
    }
  }
  if (!hasDefault) {
    graphOf(ctx, n.id + ':default', depth + 1);   // WO §2: 缺 default → default 键为空图 + warning
    cfg.default = n.id + ':default';
    exits.push({ id: n.id, label: 'default' });
    ctx.warnings.push({ code: 'W304', line: st.line, message: 'switch 缺 default, 已建空 default 子图' });
  }
  cfg.caseCount = idx;
  return { entry: n.id, exit: exits.length ? exits : [n.id] };
}
function emitSub(s, ctx) {
  emitGraph(ctx, 'sub:' + s.name, (s.body && s.body.stmts) || [], 1);
}

/* ================= 布局（附录 A2） ================= */
// main y=60 起步进 110; 子图 y=60 重起; x=深度×190+80; :false 分支整体右移 190
function autoLayout(ctx) {
  // 拓扑分层布局（2026-08-29 画布崩坏修复）：旧版把同子图全部节点排成一列（x 固定, y 逐个 +DY），
  // 顺序链被画成一条竖直瀑布、回边/跨级边全线打结。新算法：列 = 最长路径级，有界松弛收敛
  // （级封顶 N-1、最多 N 轮）——前向链水平展开，回边不倒退，纯环也确定分层且必终止。
  var keys = ctx.order || [];
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i], g = ctx.subgraphs[key];
    if (!g || !g.nodes.length) continue;
    var nodes = g.nodes, E = g.edges || [], N = nodes.length;
    var ids = {};
    for (var a = 0; a < N; a++) ids[nodes[a].id] = nodes[a];
    var lvl = {};
    for (var b = 0; b < N; b++) lvl[nodes[b].id] = 0;
    var inner = [];
    for (var c = 0; c < E.length; c++) {
      var e = E[c];
      if (ids[e.from] && ids[e.to] && e.from !== e.to) inner.push(e);
    }
    for (var round = 0; round < N; round++) {
      var changed = false;
      for (var k2 = 0; k2 < inner.length; k2++) {
        var e2 = inner[k2], cand = lvl[e2.from] + 1;
        if (cand > lvl[e2.to] && cand <= N - 1) { lvl[e2.to] = cand; changed = true; }
      }
      if (!changed) break;
    }
    var byL = {};
    for (var n2 = 0; n2 < N; n2++) (byL[lvl[nodes[n2].id]] = byL[lvl[nodes[n2].id]] || []).push(nodes[n2]);
    var x0 = (ctx.depth[key] || 0) * LAY.DX + LAY.X0 + (/:false$/.test(key) ? LAY.FALSE_SHIFT : 0);
    var lvls = Object.keys(byL).map(function (x) { return +x; }).sort(function (a, b) { return a - b; });
    for (var L = 0; L < lvls.length; L++) {
      var col = byL[lvls[L]];
      for (var r2 = 0; r2 < col.length; r2++) {
        var n = col[r2];
        n.x = x0 + L * LAY.DX;
        n.y = LAY.Y0 + r2 * LAY.DY;
        n.pos = { x: n.x, y: n.y };
      }
    }
  }
}

/* ================= 入口 ================= */
function buildFlow(ast) {
  _seq = 0;   // 重置序号 → 同输入每次构建得到稳定 id 序列 → graphHash 可复现（修正 D10 同步判据）
  var ctx = {
    nodes: [], edges: [], subgraphs: {}, depth: {}, order: [],
    graphEnd: {}, loopStack: [], loopMap: {}, loopSeq: 0, warnings: []
  };
  var main = (ast && ast.main && ast.main.stmts) || (ast && Array.isArray(ast.main) ? ast.main : []);
  emitGraph(ctx, 'main', main, 0);
  var subs = (ast && ast.subs) || [];
  for (var i = 0; i < subs.length; i++) emitSub(subs[i], ctx);
  autoLayout(ctx);
  var g = ctx.subgraphs.main, sub = {};
  Object.keys(ctx.subgraphs).forEach(function (k) { if (k !== 'main') sub[k] = ctx.subgraphs[k]; });
  g.graphHash = graphHash(g);
  Object.keys(sub).forEach(function (k) { sub[k].graphHash = graphHash(sub[k]); });
  var keys = Object.keys(sub);
  return {
    graph: g,                       // → S.graph
    subgraphs: sub,                 // → S.subgraphs
    sub: sub,                       // WO §1 骨架字段名
    nodes: ctx.nodes, edges: ctx.edges,
    warnings: ctx.warnings,
    graphHash: graphHash({ graph: g, subgraphs: sub }),
    stats: { nodes: ctx.nodes.length, edges: ctx.edges.length, graphs: keys.length + 1, keys: keys.length, keyList: keys }
  };
}

var API = {
  buildFlow: buildFlow, graphHash: graphHash, canonical: canonical,
  emitStmts: emitStmts, emitStmt: emitStmt, emitIf: emitIf, emitFor: emitFor,
  emitWhile: emitWhile, emitLoop: emitLoop, emitSwitch: emitSwitch, emitSub: emitSub,
  mkNode: mkNode, mkEdge: mkEdge, link: link, autoLayout: autoLayout, uid: mkId, LAY: LAY
};
if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (typeof window !== 'undefined') window.DslToFlow = API;

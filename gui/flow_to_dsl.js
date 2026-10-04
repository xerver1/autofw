// gui/flow_to_dsl.js — 预览图 cfg → DSL 文本(按键精灵风格); 纯函数; UMD 导出 toDsl + normalize
// 规格: WO-P3 §1–§4 / 底稿 §4.5 图→DSL 反序列化 / §6.4 normalize 实现
// 反解规则(WO §2 六条): 子图键 <id>:true/:false→if; <loopId>→for/while/loop;
//   <id>:caseN/:default→switch; sub:<名>→sub 块; break 边(忽略, 节点 op=break 即输出)
// 节点→DSL: action{op:tap/tap_image}→Tap/TapImage / delay→Delay / call→Call / log→TracePrint /
//   loop_*/if_else/switch_case→对应结构语句(按键精灵: For/Next While/Wend Do/Loop If/End If Sub/End Sub)
// 找图/找色条件行自动加 // 注释, 提升可读性(对标按键精灵代码界面)

// normalize(): 去注释/首尾空白/空行 + 压缩连续空白 (§6.4 原样抄入, 往返比对用)
function normalize(code) {
  return code.split(/\r?\n/)
    .map(function (l) { return l.replace(/\/\/.*$/, ''); })            // 去行注释
    .join('\n').replace(/\/\*[\s\S]*?\*\//g, '')                       // 去块注释
    .split(/\r?\n/).map(function (l) { return l.trim(); })             // 去首尾空白
    .filter(function (l) { return l.length > 0; })                     // 去空行
    .join('\n')
    .replace(/[ \t]{2,}/g, ' ');                                       // 压缩连续空白
}

// toDsl(flow): flow = { graph: <main 子图>, subgraphs: { <键>: {nodes,edges} } }
// 也兼容 { main, sub/subgraphs } 形态; 返回规范 DSL 文本(2 空格缩进, indent 为整数, 用 sp() 转空格)
function toDsl(flow, opts) {
  if (!flow) return '';
  function sp(n) { return n > 0 ? new Array(n + 1).join(' ') : ''; }
  // tag: 行 → {id, text}，id 为产生该行的节点身份（循环体/分支/子图节点）；行映射用
  function tag(id, text) { return { id: id, text: text }; }
  // UX-16 修复：兼容原生节点（addNode 只写 config）与 DSL 节点（cfg）两种形态
  function cfgOf(n) { return n.cfg || n.config || {}; }
  var subgraphs = {};
  var mainG = flow.graph || (flow.main && (flow.main.graph || flow.main)) || null;
  subgraphs['main'] = mainG;
  var extra = flow.subgraphs || flow.sub || {};
  Object.keys(extra).forEach(function (k) { subgraphs[k] = extra[k]; });
  if (!subgraphs['main']) return '';

  // 跨子图索引所有节点 by id (反解分支体时按 key 取回节点)
  var nodeById = {};
  // 59 轮：未连接（从 start 沿出边不可达）节点标记 —— toDsl 时整块注释，避免预留节点混入执行流。
  //   每次 toDsl 调用独立闭包，多次调用互不串味。
  var orphanMark = {};
  function commentOut(lines) {
    return lines.map(function (e) { return tag(e.id, e.text.replace(/^(\s*)(\S)/, '$1// [未连接] $2')); });
  }
  Object.keys(subgraphs).forEach(function (k) {
    var g = subgraphs[k]; if (!g || !g.nodes) return;
    g.nodes.forEach(function (n) { nodeById[n.id] = n; });
  });
  // 子图内"语句节点" = 非 start/end 的节点, 按数组序(=emitGraph 生成序, WO §2 顺序)
  function bodyStmts(key) {
    var g = subgraphs[key]; if (!g || !g.nodes) return [];
    var stmts = g.nodes.filter(function (n) { return n.type !== 'start' && n.type !== 'end'; });
    // ── 执行序修复（2026-08-27）：B2 编辑器节点数组序 ≠ 连线拓扑执行序，
    //    沿出边从 start 开始拓扑遍历，让生成代码与引擎 _walk 实际执行顺序一致；
    //    无 start/无边结构的图退回数组序兜底；断链未访问节点按数组序追加到尾部不丢失。
    var startN = null;
    for (var si = 0; si < g.nodes.length; si++) if (g.nodes[si].type === 'start') { startN = g.nodes[si]; break; }
    if (!startN || !g.edges || !g.edges.length) return stmts;
    var adj = {};
    g.edges.forEach(function (e) {
      var f = e.from || e.src, t = e.to || e.dst;
      if (!f || !t) return;
      if (!adj[f]) adj[f] = [];
      adj[f].push(t);
    });
    var stmtIds = {};
    stmts.forEach(function (n) { stmtIds[n.id] = n; });
    var visited = {}, seq = [], cur = startN.id, guard = 0;
    while (cur && guard++ < stmts.length + 50) {
      var outs = adj[cur] || [], nxt = null;
      for (var oi = 0; oi < outs.length; oi++) {
        if (stmtIds[outs[oi]] && !visited[outs[oi]]) { nxt = outs[oi]; break; }
      }
      if (!nxt) break;                               // 无可用后续（汇合 end/回环已访问）
      visited[nxt] = true;
      seq.push(stmtIds[nxt]);
      cur = nxt;
    }
    /* 2026-09-19：未连接判定改为「从 start 全边 BFS 可达」。
       上方主链 walk 只走每节点第一条出边，if/else 的另一分支会被误标 // [未连接]。
       可达但不在主链上的节点（分支侧）按 BFS/数组序追加，不注释；真正不可达才标 orphan。 */
    var reach = {};
    var bq = [startN.id];
    while (bq.length) {
      var bu = bq.shift();
      if (reach[bu]) continue;
      reach[bu] = 1;
      var bouts = adj[bu] || [];
      for (var bi = 0; bi < bouts.length; bi++) {
        if (bouts[bi] && !reach[bouts[bi]]) bq.push(bouts[bi]);
      }
    }
    stmts.forEach(function (n) {
      if (visited[n.id]) return;
      if (reach[n.id]) { visited[n.id] = true; seq.push(n); }
      else { orphanMark[n.id] = true; seq.push(n); }
    });
    return seq;
  }

  // 字符串 → DSL 引号文本（内部双引号转义）
  function q(s) { return '"' + String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'; }
  // 条件节点 → DSL 判定文本（DSL 节点有 expr 直接复用；原生节点按 kind/image/region/invert 重建）
  function condText(cfg) {
    if (cfg.expr) return String(cfg.expr).trim();
    var c = '';
    if (cfg.invert) c += 'not ';
    if (cfg.kind === 'region_match' && cfg.image && cfg.region_match) {
      var rm = cfg.region_match;
      c += 'image ' + q(cfg.image) + ' in (' + rm.x + ', ' + rm.y + ', ' + rm.w + ', ' + rm.h + ')';
    } else if (cfg.image) {
      c += 'image ' + q(cfg.image);
    } else if (cfg.text) {
      c += 'text ' + q(cfg.text);
    } else {
      c += 'true';
    }
    if (cfg.match_threshold != null && cfg.kind) c += ' threshold ' + (cfg.match_threshold / 100);
    return c;
  }
  // 条件文本 → 可读性注释(找图/找色/识别文字时自动加 // 说明)
  function condComment(t) {
    var m = /image\s+"([^"]+)"/i.exec(t) || /image\s+([^\s"]+)/i.exec(t);
    if (m) return '找到图片"' + m[1] + '"时执行';
    var tx = /text\s+"([^"]+)"/i.exec(t) || /ocr\s+"([^"]+)"/i.exec(t);
    if (tx) return '识别到文字"' + tx[1] + '"时执行';
    var col = /color\s+"?([0-9A-Fa-f]{6})"?/i.exec(t);
    if (col) return '找到颜色#' + col[1] + '时执行';
    return null;
  }

  // action 节点 → 单行 按键精灵风格命令（DSL 节点读 cfg.op/args；原生节点读 cfg.action 原语串）
  function emitAction(node, indent, ctx) {
    var cfg = cfgOf(node), op = cfg.op, args = cfg.args, act = cfg.action;
    if (node.type === 'break' || op === 'break') return sp(indent) + (ctx && ctx.loopKind === 'for' ? 'Exit For' : 'Exit Do');
    if (node.type === 'print' || op === 'print' || op === 'log') {
      var _t = cfg.text != null ? cfg.text : (cfg.msg || '');
      var _ts = String(_t).trim();
      var _out = (/^".*"$/.test(_ts) || /^'.*'$/.test(_ts)) ? _t : q(_t);
      // warn 级保留语义: TracePrint "[警告] ..."
      if (cfg.level === 'warn' && !/^\[警告\]/.test(_ts)) _out = q('[警告] ' + _ts.replace(/^"/, '').replace(/"$/, ''));
      return sp(indent) + 'TracePrint ' + _out;
    }
    if (op === 'tap_image' && cfg.image) {
      if (args && /image/.test(args)) return sp(indent) + 'TapImage ' + args.replace(/^image\s+/i, '');
      var _th = cfg.threshold != null ? ' threshold ' + cfg.threshold : '';
      return sp(indent) + 'TapImage ' + q(cfg.image) + _th;
    }
    if (op === 'tap') {
      if (args && /[(),]/.test(args)) return sp(indent) + 'Tap ' + args;     // 含表达式/坐标
      if (cfg.x != null && cfg.y != null) return sp(indent) + 'Tap ' + cfg.x + ', ' + cfg.y;
      if (args) return sp(indent) + 'Tap ' + args;
      return sp(indent) + 'Tap';
    }
    if (op === 'delay') {
      var _ms = cfg.ms != null ? cfg.ms : (args ? cfg.ms : null);
      var _mx = cfg.ms_max != null ? cfg.ms_max : null;
      if (_ms != null) return sp(indent) + 'Delay ' + _ms + (_mx != null && _mx !== _ms ? '~' + _mx : '');
      if (args) return sp(indent) + 'Delay ' + args;
      return sp(indent) + 'Delay 0';
    }
    if (op === 'wait_image' || op === 'wait_text' || op === 'wait_gone' || op === 'wait_key') {
      if (args) return sp(indent) + (op === 'wait_gone' ? 'WaitGone ' : op === 'wait_text' ? 'WaitText ' : op === 'wait_key' ? 'WaitKey ' : 'WaitImage ') + args.replace(/^image\s+/i, '');
      if (op === 'wait_key') return sp(indent) + 'WaitKey ' + q(cfg.key || '');
      return sp(indent) + (op === 'wait_gone' ? 'WaitGone ' : op === 'wait_text' ? 'WaitText ' : 'WaitImage ') + q(cfg.image || cfg.text || '');
    }
    if (op === 'call') return sp(indent) + 'Call ' + (cfg.sub || '') + '()';
    if (op === 'break') return sp(indent) + (ctx && ctx.loopKind === 'for' ? 'Exit For' : 'Exit Do');
    if (op === 'continue') return sp(indent) + 'Continue';
    if (op === 'exit_script') return sp(indent) + (ctx && ctx.inSub ? 'Exit Sub' : 'Exit');
    if (op === 'assign') return sp(indent) + 'Let ' + (cfg.var || '') + ' = ' + (cfg.expr || '');
    if (op === 'arr_push') return sp(indent) + 'Push ' + (cfg.var || '?') + ', ' + (cfg.expr || '');
    if (op === 'am_start') return sp(indent) + 'AppStart ' + (cfg.pkg ? q(cfg.pkg) : '');
    if (op === 'am_stop') return sp(indent) + 'AppStop ' + (cfg.pkg ? q(cfg.pkg) : '');
    if (op === 'emulator') return sp(indent) + 'Emulator' + (args ? ' ' + args : '');
    if (op === 'screenshot') return sp(indent) + 'SnapShot ' + (cfg.file ? q(cfg.file) : (args ? q(args) : ''));
    if (op === 'swipe') return sp(indent) + 'Swipe ' + (args ? args : '');
    if (op === 'key') return sp(indent) + 'KeyPress ' + (cfg.key ? q(cfg.key) : (args ? args : ''));
    if (op === 'key_hold') return sp(indent) + 'KeyPress ' + (cfg.key ? q(cfg.key) : '') + '  // 按住';
    if (op === 'type') return sp(indent) + 'SayString ' + (cfg.text ? q(cfg.text) : (args || ''));
    if (op === 'input_text') return sp(indent) + 'SayString ' + (cfg.text ? q(cfg.text) : (args || ''));
    if (op === 'find' || op === 'find_all' || op === 'match_image' || op === 'match_color' || op === 'match_multi_color') {
      if (args) return sp(indent) + 'FindImage ' + args.replace(/^image\s+/i, '');
      return sp(indent) + 'FindImage ' + q(cfg.image || cfg.colors || '');
    }
    if (op === 'ocr' || op === 'ocr_text' || op === 'ocr_number' || op === 'ocr_contains') return sp(indent) + 'Ocr ' + (args ? args : (cfg.text ? q(cfg.text) : ''));
    if (op === 'wait') return sp(indent) + 'WaitImage ' + (args ? args.replace(/^image\s+/i, '') : q(cfg.image || ''));
    // 原生 action 节点：cfg.action = 原语串（"am_start" / "adb_tap 960 1008" / "swipe ..."）
    if (act) {
      var w = String(act).split(/\s+/);
      var prim = w[0].toLowerCase();
      if (prim === 'am_start') return sp(indent) + 'AppStart' + (w[1] ? ' ' + q(w[1]) : '');
      if (prim === 'am_stop') return sp(indent) + 'AppStop' + (w[1] ? ' ' + q(w[1]) : '');
      if (prim === 'adb_tap') {
        if (cfg.tap_img) return sp(indent) + 'TapImage ' + q(cfg.tap_img);
        return sp(indent) + 'Tap ' + (cfg.tap_x != null ? cfg.tap_x : (w[1] || 0)) + ', ' + (cfg.tap_y != null ? cfg.tap_y : (w[2] || 0));
      }
      if (prim === 'tap_image') return sp(indent) + 'TapImage ' + (cfg.image ? q(cfg.image) : (w.slice(1).join(' ') ? w.slice(1).join(' ') : '')) + (cfg.threshold != null ? ' threshold ' + cfg.threshold : '');
      if (prim === 'find_image' || prim === 'find') return sp(indent) + 'FindImage ' + (cfg.image ? q(cfg.image) : (w.slice(1).join(' ') ? w.slice(1).join(' ') : ''));
      if (prim === 'wait_image') return sp(indent) + 'WaitImage ' + (cfg.image ? q(cfg.image) : (w.slice(1).join(' ') ? w.slice(1).join(' ') : ''));
      if (prim === 'screenshot') return sp(indent) + 'SnapShot ' + (cfg.file ? q(cfg.file) : (w.slice(1).join(' ') ? q(w.slice(1).join(' ')) : ''));
      if (prim === 'ocr' || prim === 'ocr_check') return sp(indent) + 'Ocr ' + (cfg.text ? q(cfg.text) : (w.slice(1).join(' ') ? w.slice(1).join(' ') : ''));
      if (prim === 'delay') return sp(indent) + 'Delay ' + (w[1] ? w[1] : '0');
      if (prim === 'wait') return sp(indent) + 'Delay ' + (w[1] || '0');
      if (prim === 'wait_gone') return sp(indent) + 'WaitGone ' + (w.slice(1).join(' ') ? w.slice(1).join(' ') : '');
      if (prim === 'swipe') return sp(indent) + 'Swipe ' + w.slice(1).join(' ');
      if (prim === 'key') return sp(indent) + 'KeyPress ' + q(w.slice(1).join(' ') || '');
      return sp(indent) + act;
    }
    // 兜底: B2 原生节点(无 cfg.op/action)→ 用节点名称输出为注释行, 避免 'unknown' 污染 DSL
    var label = (node.text || node.name || (typeof op === 'string' ? op : 'step'));
    return sp(indent) + '// ' + label + (args ? ' ' + args : '');
  }

  // 递归走子图(按节点数组序, 控制流节点递归进其分支子图键); ctx 传播 loopKind/inSub
  function walkBody(key, indent, ctx) {
    ctx = ctx || { loopKind: null, inSub: false, inOrphan: false };
    var out = [], stmts = bodyStmts(key);
    for (var i = 0; i < stmts.length; i++) {
      var n = stmts[i];
      // 59 轮：未连接节点整块注释；容器节点向下传 inOrphan=true，避免其子块二次注释
      var orphan = !ctx.inOrphan && !!orphanMark[n.id];
      var c = orphan ? { loopKind: ctx.loopKind, inSub: ctx.inSub, inOrphan: true } : ctx;
      var produced;
      if (n.type === 'if_else' || n.type === 'else_if' || n.type === 'condition') produced = emitIfChain(n.id, indent, c, key);
      else if (n.type === 'loop_for') produced = emitLoop(n, indent, c, 'for');
      else if (n.type === 'loop_while') produced = emitLoop(n, indent, c, 'while');
      else if (n.type === 'loop_do_while') produced = emitLoop(n, indent, c, 'loop');
      else if (n.type === 'switch_case') produced = emitSwitch(n, indent, c);
      else produced = [tag(n.id, emitAction(n, indent, c))];
      if (orphan) produced = commentOut(produced);
      out = out.concat(produced);
    }
    return out;
  }

  // if 链(WO §2 ③④⑥): false 分支若仅含单个 if_else → 折叠为 Else If; 空 false → 不带 Else
  function emitIfChain(ifId, indent, ctx, gkey) {
    var out = [], cur = ifId, first = true;
    function _isBranch(n) { return n && (n.type === 'if_else' || n.type === 'else_if' || n.type === 'condition'); }
    while (cur) {
      var node = nodeById[cur]; if (!node) break;
      var cond = String(condText(cfgOf(node))).trim();
      var cmt = condComment(cond);
      if (first) { out.push(tag(cur, sp(indent) + 'If ' + cond + ' Then' + (cmt ? '  // ' + cmt : ''))); first = false; }
      else out.push(tag(cur, sp(indent) + 'ElseIf ' + cond + ' Then' + (cmt ? '  // ' + cmt : '')));
      out = out.concat(walkBody(cur + ':true', indent + 2, ctx));
      var fbody = bodyStmts(cur + ':false', gkey);
      // false 分支体里仅一个判定节点 → 折叠为 Else If
      if (fbody.length === 1 && _isBranch(fbody[0])) cur = fbody[0].id;
      else if (fbody.length === 0) {
        // 画布 FALSE 出边直接接到下一个判定节点（else_if 链）也折叠为 Else If
        var g0 = (typeof gkey === 'string' && subgraphs && subgraphs[gkey]) || mainG;
        var fe = null;
        if (g0 && g0.edges) {
          for (var ei = 0; ei < g0.edges.length; ei++) {
            var ed = g0.edges[ei];
            var ef = ed.from || ed.src, et = ed.to || ed.dst;
            if (ef === cur && ed.label === 'false' && nodeById[et] && _isBranch(nodeById[et])) { fe = nodeById[et]; break; }
          }
        }
        if (fe) cur = fe.id;
        else cur = null;
      }
      else { out.push(tag(cur, sp(indent) + 'Else')); out = out.concat(walkBody(cur + ':false', indent + 2, ctx)); cur = null; }
    }
    out.push(tag(ifId, sp(indent) + 'End If'));
    return out;
  }

  // 循环(WO §2 ①): 按键精灵风格 — For/Next, While/Wend, Do/Loop
  function emitLoop(node, indent, ctx, kind) {
    var cfg = cfgOf(node), out = [], head;
    if (kind === 'for')
      head = 'For ' + (cfg.var || 'i') + ' = ' + (cfg.from || 1) + ' To ' + (cfg.count != null ? cfg.count : (cfg.to || 10)) + (cfg.step ? ' Step ' + cfg.step : '');
    else if (kind === 'while')
      head = 'While ' + condText(cfg);
    else
      head = 'Do';
    out.push(tag(node.id, sp(indent) + head + ((cfg.delay_ms | 0) > 0 ? ' delay ' + cfg.delay_ms : '')));
    var childCtx = { loopKind: kind === 'while' ? 'while' : (kind === 'for' ? 'for' : 'loop'), inSub: ctx.inSub, inOrphan: !!ctx.inOrphan };
    out = out.concat(walkBody(node.id, indent + 2, childCtx));
    var endKw = kind === 'for' ? 'Next' : (kind === 'while' ? 'Wend' : 'Loop');
    out.push(tag(node.id, sp(indent) + endKw));
    return out;
  }

  // switch(WO §2 ①⑤): 各 case 一键 + default 键; 缺 default(空键)→不输出 default 分支
  function emitSwitch(node, indent, ctx) {
    var cfg = cfgOf(node), out = [];
    out.push(tag(node.id, sp(indent) + 'Switch ' + (cfg.expr || '')));
    var cases = cfg.cases || [];
    for (var i = 0; i < cases.length; i++) {
      var cbody = walkBody(cases[i].key, indent + 2, ctx);
      var cval = (cases[i].value != null ? cases[i].value : '');
      if (cbody.length) { out.push(tag(node.id, sp(indent) + '  Case ' + cval + ': ' + cbody[0].text)); out = out.concat(cbody.slice(1)); }
      else out.push(tag(node.id, sp(indent) + '  Case ' + cval + ':'));
    }
    if (cfg.default) {
      var dbody = walkBody(cfg.default, indent + 2, ctx);
      if (dbody.length) { out.push(tag(node.id, sp(indent) + '  Default: ' + dbody[0].text)); out = out.concat(dbody.slice(1)); }
      else out.push(tag(node.id, sp(indent) + '  Default:'));
    }
    out.push(tag(node.id, sp(indent) + 'End Switch'));
    return out;
  }

  // 主流程: main 在前, sub 在后(与源 T5 顺序一致, 满足文本往返; §5.3 顺序以裁决为准)
  var tagged = [tag(null, 'main')];
  tagged = tagged.concat(walkBody('main', 2, { loopKind: null, inSub: false }));
  tagged.push(tag(null, 'end main'));
  Object.keys(subgraphs).filter(function (k) { return k.indexOf('sub:') === 0; }).forEach(function (k) {
    tagged.push(tag(null, 'Sub ' + k.slice(4) + '()'));
    tagged = tagged.concat(walkBody(k, 2, { loopKind: null, inSub: true }));
    tagged.push(tag(null, 'End Sub'));
  });
  var lines = tagged.map(function (e) { return e.text; });
  var code = lines.join('\n');
  // 行映射：绝对行号 → 产生该行的节点身份（容器节点记首行 head；用于点击定位精确匹配）
  if (opts && opts.lineMap) {
    var lm = tagged.map(function (e, i) { return { line: i + 1, nodeId: e.id }; })
      .filter(function (e) { return e.nodeId !== null && e.nodeId !== undefined; });
    return { code: code, lineMap: lm };
  }
  return code;
}

// toDslWithMap(flow): 同 toDsl，但返回 { code, lineMap }，lineMap=[{line,nodeId}]。
// 供前端「点击节点→代码定位」按节点身份精确映射（解决多 while/start 命中首个的问题），
// 不破坏 toDsl 既有字符串输出与签名。
function toDslWithMap(flow) {
  return toDsl(flow, { lineMap: true });
}

  // 单节点 → DSL：拖拽「预设模板 / 已配置节点」到代码编辑器时生成源码。
  // 直接复用既有 toDsl 管线（把单节点包成合成图），结构化节点按空分支体输出模板头。
  function nodeToDsl(node, indent) {
    indent = indent || 0;
    if (!node) return '';
    // 合成 id：条件/循环等节点靠 nodeById[cur] 反解分支体，缺 id 会导致 If 头丢失
    var nn = node;
    if (!nn.id) { nn = Object.assign({}, node); nn.id = 'n_drag_' + Date.now(); }
    var flow = { graph: { nodes: [nn], edges: [] }, subgraphs: {} };
    var full = toDsl(flow);                 // "main\n  <dsl>\nend main"
    var lines = full.split('\n');
    if (lines.length && /^\s*main\s*$/.test(lines[0])) lines.shift();
    if (lines.length && /^\s*end\s+main\s*$/.test(lines[lines.length - 1])) lines.pop();
    // 去掉 toDsl 主流程默认 2 空格缩进
    var body = lines.map(function (l) { return l.replace(/^  /, ''); }).join('\n');
    if (indent > 0) {
      var pad = new Array(indent + 1).join(' ');
      body = body.split('\n').map(function (l) { return l.length ? pad + l : l; }).join('\n');
    }
    return body;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { toDsl: toDsl, normalize: normalize, nodeToDsl: nodeToDsl, toDslWithMap: toDslWithMap };
  if (typeof window !== 'undefined') window.FlowToDsl = { toDsl: toDsl, normalize: normalize, nodeToDsl: nodeToDsl, toDslWithMap: toDslWithMap };

// gui/dsl_parser.js — 纯函数, 无 DOM, Node/浏览器双可用 (UMD 导出)
'use strict';

// 去 // 行注释与 /* */ 块注释（保留换行维持行号）
function stripComments(src) {
  let out = '', i = 0; const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') { while (i < n && src[i] !== '\n') { out += ' '; i++; } }
    else if (c === '/' && src[i + 1] === '*') {
      out += ' '; i += 2;
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) { out += (src[i] === '\n') ? '\n' : ' '; i++; }
      if (i < n) { out += '  '; i += 2; }
    } else { out += (c === '\n') ? '\n' : c; i++; }
  }
  return out;
}

// 折行合并: 括号深度>0 合并下一行; 诊断行号取首行 (勘误 #19)
function mergeContinuedLines(src) {
  const raw = src.split('\n'); const lines = []; let i = 0;
  while (i < raw.length) {
    let text = raw[i], line = i + 1, depth = 0;
    for (const ch of text) { if (ch === '(') depth++; else if (ch === ')') depth--; }
    while (depth > 0 && i + 1 < raw.length) { i++; text += ' ' + raw[i]; for (const ch of raw[i]) { if (ch === '(') depth++; else if (ch === ')') depth--; } }
    lines.push({ text, line }); i++;
  }
  return { lines };
}

const NUMBER_TIME = /[0-9]+(ms|s)(~[0-9]+(ms|s))?/;   // 含区间 ~ (文末 B2)
// 关键字大小写不敏感(按键精灵风格: If/For/While/Do/...); do/next/wend 为新增循环关键字
const KEYWORDS = ['if', 'else', 'elseif', 'for', 'while', 'loop', 'do', 'next', 'wend', 'switch', 'case', 'default', 'break', 'sub', 'end', 'return', 'log', 'warn', 'exit', 'continue'];
// 原语(命令式动词): 旧名 + 按键精灵别名, 全部小写; 解析时按 .toLowerCase() 匹配
const PRIMITIVES = new Set([
  'click', 'double_click', 'long_press', 'swipe', 'drag', 'pinch', 'key', 'key_hold', 'type', 'find', 'find_all',
  'exists', 'count', 'wait', 'wait_gone', 'screenshot', 'color', 'colors', 'color_at', 'ocr', 'ocr_exists', 'ocr_number',
  'text', 'delay', 'app_start', 'app_stop', 'app_current', 'emulator', 'push', 'read', 'file',
  'tap', 'tapimage', 'doubletap', 'longtap', 'keypress', 'saystring', 'findimage', 'waitimage', 'waitgone', 'waittext',
  'waitkey', 'snapshot', 'appstart', 'appstop', 'traceprint'
]);

// tokenize: 每行扫描, 行末补 NL; 未闭合字符串报 E102
function tokenize(lines, diags) {
  const toks = [];
  const RE = /("(?:[^"\\]|\\.)*"?)|(#[0-9A-Fa-f]{6})|(\d+(?:\.\d+)?(?:ms|s)(?:~\d+(?:\.\d+)?(?:ms|s))?)|(\d+\.\d+)|(\d+)|(->|==|!=|>=|<=|[-+*/%<>=!])|([()\[\]{},:;])|([A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*)/g;
  for (const ln of lines) {
    let m; RE.lastIndex = 0;
    while ((m = RE.exec(ln.text)) !== null) {
      const t = m[0]; let type, val = t;
      if (m[1] !== undefined) {
        if (!t.endsWith('"')) { toks.push({ t: 'STR', v: t, line: ln.line }); toks.push({ t: 'NL', v: '', line: ln.line }); if (diags) diags.push(diag('E102', ln.line, '未闭合字符串')); RE.lastIndex = ln.text.length; continue; }
        type = 'STR';
      } else if (m[2] !== undefined) type = 'HEX';
      else if (m[3] !== undefined) type = 'TIME';
      else if (m[4] !== undefined || m[5] !== undefined) type = 'NUM';
      else if (m[6] !== undefined || m[7] !== undefined) type = 'OP';
      else type = (KEYWORDS.indexOf(t.toLowerCase()) >= 0) ? 'KW' : 'ID';
      toks.push({ t: type, v: val, line: ln.line });
    }
    toks.push({ t: 'NL', v: '', line: ln.line });
  }
  return toks;
}

function diag(code, line, msg) { return { code, severity: 'error', line, col: 1, message: msg }; }

// 两遍扫描: 第一遍收集 sub 名检 E204; 第二遍建 AST 并检 E205/E202 等
function parseProgram(tokens) {
  const diags = []; const subNames = new Set();
  for (let k = 0; k < tokens.length; k++) {
    const t = tokens[k];
    if (t.t === 'KW' && t.v.toLowerCase() === 'sub' && tokens[k + 1] && (tokens[k + 1].t === 'ID' || tokens[k + 1].t === 'KW')) {
      const nm = tokens[k + 1].v;
      if (subNames.has(nm)) diags.push(diag('E204', tokens[k + 1].line, '重复定义 sub: ' + nm));
      else subNames.add(nm);
    }
  }
  const p = { tokens, i: 0, diags, ctrl: [], count: 0, maxDepth: 0, subNames, loopSeq: 0, loopStack: [] };
  const main = []; const subs = [];
  // 关键字/标识符比较均大小写不敏感(按键精灵风格: If/For/While/Do/End If 等)
  const isKW = (tk, k) => tk && tk.t === 'KW' && tk.v.toLowerCase() === k;
  const isId = (tk, s) => tk && tk.t === 'ID' && tk.v.toLowerCase() === s;
  const isOp = (tk, o) => tk && tk.t === 'OP' && tk.v === o;
  const peekType = () => { const t = p.tokens[p.i + 1]; return t ? t.v.toLowerCase() : null; };
  const pushCtrl = (k) => { p.ctrl.push(k); if (p.ctrl.length > p.maxDepth) p.maxDepth = p.ctrl.length; };
  const popCtrl = () => p.ctrl.pop();
  const skipToNL = () => { while (p.i < p.tokens.length && p.tokens[p.i].t !== 'NL') p.i++; };
  function emitEndError(expected, actual, line) {
    if (expected && actual && expected !== actual) diags.push(diag('E208', line, 'end 类型错配: 期望 ' + expected + ' 实得 ' + actual));
    else diags.push(diag('E105', line, 'end 不匹配/多余: ' + (actual ? 'end ' + actual : 'end')));
  }
  // 表达式: 直到 NL/停止词; 括号深度>0 收尾 => 不完整(E106)
  function parseExpr(stopWords) {
    stopWords = stopWords || [];
    const tk0 = (tk) => tk && (tk.t === 'KW' || tk.t === 'ID' || tk.t === 'OP') && stopWords.indexOf(tk.v.toLowerCase()) >= 0;
    const toks = []; let depth = 0, incomplete = false;
    while (p.i < p.tokens.length) {
      const tk = p.tokens[p.i];
      if (tk.t === 'NL' || isKW(tk, 'end') || isKW(tk, 'else') || tk0(tk)) break;
      if (tk.t === 'OP' && (tk.v === '(' || tk.v === '[')) depth++;
      else if (tk.t === 'OP' && (tk.v === ')' || tk.v === ']')) depth--;
      toks.push(tk); p.i++;
    }
    if (depth !== 0) incomplete = true;
    return { tokens: toks, incomplete };
  }
  function parseBlock(expectedEnd, extraStop) {
    const stmts = [];
    while (p.i < p.tokens.length) {
      const tk = p.tokens[p.i];
      if (tk.t === 'NL') { p.i++; continue; }
      // 类型化循环终止符(按键精灵: Next/Wend/Loop); 仅当与最内层控制流匹配才闭合
      if (isKW(tk, 'next') || isKW(tk, 'wend') || isKW(tk, 'loop')) {
        const top = p.ctrl[p.ctrl.length - 1];
        const okClose = (isKW(tk, 'next') && top === 'for') || (isKW(tk, 'wend') && top === 'while') || (isKW(tk, 'loop') && top === 'do');
        if (okClose) { p.i++; return { stmts, closed: true }; }
        // 非闭合: next/wend 出现在错误位置 → 报错跳过; loop 可能是旧语法起始 → 落入 parseStmt 正常处理
        if (!isKW(tk, 'loop')) { emitEndError(null, tk.v.toLowerCase(), tk.line); p.i++; continue; }
      }
      if (isKW(tk, 'end')) {
        const et = peekType();
        if (expectedEnd !== null && et === expectedEnd) { p.i += 2; return { stmts, closed: true }; }
        if (et === '' || et === null) { p.i += 1; return { stmts, closed: true }; }  // 裸 end 收尾（裁决用例统一用裸 end）
        emitEndError(expectedEnd, et, tk.line); p.i += (et ? 2 : 1); continue;
      }
      if (extraStop && extraStop(tk)) return { stmts, closed: false };
      const st = parseStmt();
      if (st) { stmts.push(st); p.count++; }
    }
    return { stmts, closed: false };
  }
  function parseStmt() {
    const tk = p.tokens[p.i];
    if (!tk) return null;
    if (isKW(tk, 'if')) return parseIf();
    if (isKW(tk, 'for')) return parseFor();
    if (isKW(tk, 'while')) return parseWhile();
    if (isKW(tk, 'do')) return parseDo();
    if (isKW(tk, 'loop')) return parseLoop();
    if (isKW(tk, 'switch')) return parseSwitch();
    if (isKW(tk, 'case')) return parseCase(false);
    if (isKW(tk, 'default')) return parseCase(true);
    if (isKW(tk, 'break')) return parseBreak();
    if (isKW(tk, 'return')) { p.i++; skipToNL(); return { kind: 'return', line: tk.line }; }
    if (isKW(tk, 'exit')) return parseExit();
    if (isKW(tk, 'continue')) { p.i++; skipToNL(); return { kind: 'continue', line: tk.line }; }
    if (isKW(tk, 'log') || isKW(tk, 'warn') || isId(tk, 'traceprint')) return parseLog(tk);
    if (tk.t === 'ID' && tk.v.toLowerCase() === 'let') return parseLet();
    if (tk.t === 'ID' && tk.v.toLowerCase() === 'call') return parseCall();
    if (tk.t === 'ID' && tk.v.toLowerCase() === 'push') { p.i++; skipToNL(); return { kind: 'push', line: tk.line }; }
    if (tk.t === 'ID' && PRIMITIVES.has(tk.v.toLowerCase())) return parsePrim();
    diags.push(diag('E107', tk.line, '无法识别语句首词: ' + tk.v));
    skipToNL(); return null;
  }
  function parseIf() {
    const line = p.tokens[p.i].line; p.i++;
    const cond = parseExpr(['then']);
    const th = p.tokens[p.i];
    if (isKW(th, 'then') || isId(th, 'then')) p.i++;
    pushCtrl('if');
    const stopIf = (tk) => (tk && tk.t === 'KW' && (tk.v.toLowerCase() === 'else' || tk.v.toLowerCase() === 'elseif')) || (isKW(tk, 'end') && peekType() === 'if');
    const thenBody = parseBlock('if', stopIf);
    popCtrl();
    const elseIfs = []; let elseBody = null, closed = false;
    const term = p.tokens[p.i];
    const isElseOrEI = (tk) => tk && tk.t === 'KW' && (tk.v.toLowerCase() === 'else' || tk.v.toLowerCase() === 'elseif');
    if (isElseOrEI(term)) {
      while (isElseOrEI(p.tokens[p.i])) {
        const isEI = p.tokens[p.i].v.toLowerCase() === 'elseif';
        p.i++;
        if (isEI) {
          const c2 = parseExpr(['then']);
          const t2 = p.tokens[p.i];
          if (isKW(t2, 'then') || isId(t2, 'then')) p.i++;
          const b2 = parseBlock('if', stopIf);
          elseIfs.push({ cond: c2, body: { stmts: b2.stmts } });
          if (b2.closed) { closed = true; break; }
        } else if (p.tokens[p.i] && (isKW(p.tokens[p.i], 'if') || isId(p.tokens[p.i], 'if'))) {
          p.i++; const c2 = parseExpr(['then']);
          const t2 = p.tokens[p.i];
          if (isKW(t2, 'then') || isId(t2, 'then')) p.i++;
          const b2 = parseBlock('if', stopIf);
          elseIfs.push({ cond: c2, body: { stmts: b2.stmts } });
          if (b2.closed) { closed = true; break; }
        } else {
          const b3 = parseBlock('if', (tk) => isKW(tk, 'end') && peekType() === 'if');
          elseBody = { stmts: b3.stmts };
          if (b3.closed) closed = true;
          break;
        }
      }
    } else closed = thenBody.closed;
    if (!closed) diags.push(diag('E105', line, 'if 缺 end'));
    return { kind: 'if', line, cond, then: { stmts: thenBody.stmts }, elseIfs, elseBody };
  }
  // 从条件/表达式 token 序列尾部提取 `delay <ms>` 循环迭代等待子句（按键精灵风格的 while/do/for 行尾可附带）:
  // 就地弹出这两个 token, 返回毫秒数或 null。不影响条件本身, 仅把"迭代等待延时"从代码行读回。
  function extractLoopDelay(tokens) {
    if (!tokens || tokens.length < 2) return null;
    var last = tokens[tokens.length - 1], prev = tokens[tokens.length - 2];
    if (last.t === 'NUM' && prev.t === 'ID' && prev.v.toLowerCase() === 'delay') {
      tokens.pop(); tokens.pop();
      var v = parseInt(last.v, 10);
      return isNaN(v) ? null : v;
    }
    return null;
  }
  function parseFor() {
    const line = p.tokens[p.i].line; p.i++;
    const varT = p.tokens[p.i]; p.i++;
    if (isOp(p.tokens[p.i], '=')) p.i++;
    const from = parseExpr(['to']);
    const toT = p.tokens[p.i];
    if (isId(toT, 'to')) p.i++; else diags.push(diag('E105', line, 'for 缺 to'));
    const to = parseExpr(['step']);
    let step = null;
    if (isId(p.tokens[p.i], 'step')) { p.i++; step = parseExpr([]); }
    var delay = extractLoopDelay(to.tokens);
    if (delay == null && step) delay = extractLoopDelay(step.tokens);
    pushCtrl('for');
    const loopId = ++p.loopSeq; p.loopStack.push(loopId);
    const body = parseBlock('for', null);
    p.loopStack.pop(); popCtrl();
    if (!body.closed) diags.push(diag('E105', line, 'for 缺 end'));
    return { kind: 'for', line, var: varT.v, from, to, step, delay: delay, body: { stmts: body.stmts } };
  }
  function parseWhile() {
    const line = p.tokens[p.i].line; p.i++;
    const _raw = parseExpr([]);
    let invert = false, toks = _raw.tokens;
    if (toks.length && toks[0].t === 'ID' && toks[0].v.toLowerCase() === 'not') { invert = true; toks = toks.slice(1); }
    const cond = { tokens: toks, incomplete: _raw.incomplete };
    if (cond.incomplete) diags.push(diag('E106', line, '表达式不完整'));
    const delay = extractLoopDelay(cond.tokens);
    pushCtrl('while');
    const loopId = ++p.loopSeq; p.loopStack.push(loopId);
    const body = parseBlock('while', null);
    p.loopStack.pop(); popCtrl();
    if (!body.closed) diags.push(diag('E105', line, 'while 缺 end'));
    return { kind: 'while', line, cond, invert: invert, delay: delay, body: { stmts: body.stmts } };
  }
  function parseLoop() {
    const line = p.tokens[p.i].line; p.i++;
    pushCtrl('loop');
    const loopId = ++p.loopSeq; p.loopStack.push(loopId);
    const body = parseBlock('loop', null);
    p.loopStack.pop(); popCtrl();
    if (!body.closed) diags.push(diag('E105', line, 'loop 缺 end'));
    return { kind: 'loop', line, body: { stmts: body.stmts } };
  }
  // Do [While <cond> | Until <cond>] ... Loop  (按键精灵无限/条件循环)
  // 注: Do While/Until 统一解析为 while 节点(与 While...Wend 语义等价), 保证往返 AST 一致
  function parseDo() {
    const line = p.tokens[p.i].line; p.i++;
    if (p.tokens[p.i] && (isKW(p.tokens[p.i], 'while') || isId(p.tokens[p.i], 'while'))) {
      p.i++; const cond = parseExpr([]); if (cond.incomplete) diags.push(diag('E106', line, '表达式不完整'));
      const delay = extractLoopDelay(cond.tokens);
      pushCtrl('do'); const loopId = ++p.loopSeq; p.loopStack.push(loopId);
      const body = parseBlock('do', null); p.loopStack.pop(); popCtrl();
      if (!body.closed) diags.push(diag('E105', line, 'do while 缺 loop'));
      return { kind: 'while', line, cond, invert: false, delay: delay, body: { stmts: body.stmts } };
    }
    if (p.tokens[p.i] && (isKW(p.tokens[p.i], 'until') || isId(p.tokens[p.i], 'until'))) {
      p.i++; const cond = parseExpr([]); if (cond.incomplete) diags.push(diag('E106', line, '表达式不完整'));
      const delay = extractLoopDelay(cond.tokens);
      pushCtrl('do'); const loopId = ++p.loopSeq; p.loopStack.push(loopId);
      const body = parseBlock('do', null); p.loopStack.pop(); popCtrl();
      if (!body.closed) diags.push(diag('E105', line, 'do until 缺 loop'));
      return { kind: 'while', line, cond, invert: true, delay: delay, body: { stmts: body.stmts } };
    }
    // 裸 Do ... Loop (无限循环)
    pushCtrl('do');
    const loopId = ++p.loopSeq; p.loopStack.push(loopId);
    const body = parseBlock('do', function (tk) { return isKW(tk, 'loop'); });
    p.loopStack.pop(); popCtrl();
    if (!body.closed) diags.push(diag('E105', line, 'do 缺 loop'));
    return { kind: 'do', line, cond: null, doKind: 'loop', body: { stmts: body.stmts } };
  }
  function parseSwitch() {
    const line = p.tokens[p.i].line; p.i++;
    const cond = parseExpr([]);
    pushCtrl('switch');
    const body = parseBlock('switch', null);
    popCtrl();
    if (!body.closed) diags.push(diag('E105', line, 'switch 缺 end'));
    return { kind: 'switch', line, cond, body: { stmts: body.stmts } };
  }
  function parseCase(isDefault) {
    const line = p.tokens[p.i].line; p.i++;
    const label = isDefault ? null : parseExpr([':']);
    if (isOp(p.tokens[p.i], ':')) p.i++;
    const tk = p.tokens[p.i];
    let stmt = null;
    if (tk && tk.t !== 'NL' && !isKW(tk, 'end') && !isKW(tk, 'case') && !isKW(tk, 'default')) stmt = parseStmt();
    else diags.push(diag('E209', line, 'case 分支为空且无注释'));
    return { kind: isDefault ? 'default' : 'case', line, label, stmt };
  }
  function parseSub() {
    const line = p.tokens[p.i].line; p.i++;
    const nameT = p.tokens[p.i]; p.i++;
    const params = [];
    if (isOp(p.tokens[p.i], '(')) {
      p.i++;
      while (p.tokens[p.i] && !isOp(p.tokens[p.i], ')')) { if (p.tokens[p.i].t === 'ID') params.push(p.tokens[p.i].v); p.i++; }
      if (isOp(p.tokens[p.i], ')')) p.i++;
    }
    pushCtrl('sub');
    const body = parseBlock('sub', null);
    popCtrl();
    if (!body.closed) diags.push(diag('E105', line, 'sub 缺 end'));
    return { kind: 'subdef', name: nameT.v, params, line, body: { stmts: body.stmts } };
  }
  function parseMain() {
    const line = p.tokens[p.i].line; p.i++;
    const body = parseBlock('main', null);
    if (!body.closed) diags.push(diag('E105', line, 'main 缺 end'));
    return { stmts: body.stmts };
  }
  function parseLet() {
    const line = p.tokens[p.i].line; p.i++;
    const nameT = p.tokens[p.i]; p.i++;
    if (isOp(p.tokens[p.i], '=')) p.i++; else diags.push(diag('E106', line, 'let 缺 ='));
    const expr = parseExpr([]);
    if (expr.incomplete) diags.push(diag('E106', line, '表达式不完整'));
    return { kind: 'let', line, name: nameT.v, value: expr.tokens };
  }
  function parseCall() {
    const line = p.tokens[p.i].line; p.i++;
    const nameT = p.tokens[p.i]; p.i++;
    const args = [];
    if (isOp(p.tokens[p.i], '(')) {
      p.i++;
      while (p.tokens[p.i] && !isOp(p.tokens[p.i], ')')) { args.push(p.tokens[p.i]); p.i++; }
      if (isOp(p.tokens[p.i], ')')) p.i++;
    }
    if (!p.subNames.has(nameT.v)) diags.push(diag('E205', line, 'call 未定义: ' + nameT.v));
    return { kind: 'call', line, sub: nameT.v, args };
  }
  function parseBreak() {
    const line = p.tokens[p.i].line; p.i++;
    const allowed = p.ctrl.some((c) => c === 'for' || c === 'while' || c === 'loop' || c === 'do' || c === 'switch');
    if (!allowed) diags.push(diag('E202', line, 'break 越位（不在循环/case 内）'));
    const loopId = p.loopStack.length ? p.loopStack[p.loopStack.length - 1] : null;
    skipToNL();
    return { kind: 'break', line, loopId };
  }
  // Exit [For | Do | While | Loop | Sub | Script]  (按键精灵跳出循环/子程序; 裸 Exit = 结束脚本)
  function parseExit() {
    const line = p.tokens[p.i].line; p.i++;
    const nxt = p.tokens[p.i];
    let kind = 'exit';
    if (nxt && (isKW(nxt, 'for') || isKW(nxt, 'while') || isKW(nxt, 'do') || isKW(nxt, 'loop'))) { p.i++; kind = 'break'; }
    else if (nxt && (isKW(nxt, 'sub') || isKW(nxt, 'script'))) { p.i++; kind = 'return'; }
    const loopId = (kind === 'break' && p.loopStack.length) ? p.loopStack[p.loopStack.length - 1] : null;
    skipToNL();
    return { kind: kind, line, loopId: loopId };
  }
  function parseLog(tk) {
    const line = tk.line; const level = isKW(tk, 'warn') ? 'warn' : 'info'; p.i++;
    const expr = parseExpr([]);
    if (expr.incomplete) diags.push(diag('E106', line, '表达式不完整'));
    return { kind: 'log', line, level, text: expr.tokens };
  }
  function parsePrim() {
    const line = p.tokens[p.i].line; const name = p.tokens[p.i].v.toLowerCase(); p.i++;
    const expr = parseExpr([]);
    if (expr.incomplete) diags.push(diag('E106', line, '表达式不完整'));
    return { kind: 'prim', line, name, args: expr.tokens };
  }
  while (p.i < p.tokens.length) {
    const tk = p.tokens[p.i];
    if (tk.t === 'NL') { p.i++; continue; }
    if (isKW(tk, 'end')) { const et = peekType(); emitEndError(null, et, tk.line); p.i += (et ? 2 : 1); continue; }
    if (isKW(tk, 'sub')) { const s = parseSub(); if (s) subs.push(s); continue; }
    if (isId(tk, 'main')) { const m = parseMain(); if (m) main.push.apply(main, m.stmts); continue; }
    const st = parseStmt();
    if (st) { main.push(st); p.count++; }
  }
  let nl = 0; for (const t of tokens) if (t.t === 'NL') nl++;
  return { ok: diags.length === 0, subs, main: { stmts: main }, diagnostics: diags.slice(0, 10), stats: { lines: nl, statements: p.count, maxDepth: p.maxDepth } };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { stripComments, mergeContinuedLines, tokenize, parseProgram, diag };
if (typeof window !== 'undefined') window.DslParser = { stripComments, mergeContinuedLines, tokenize, parseProgram, diag };

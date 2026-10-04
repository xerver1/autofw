// gui/export_md.js — graph(flowJson) → Markdown; 纯函数; UMD 导出 buildMd
// 规格: WO-P4P5 §1 / 主审裁决(六段固定顺序 + 每语句行 <!-- L<line> -->)
// 输入: buildMd(flow, meta)
//   flow = { graph:<main 子图>, subgraphs:{ <键>:{nodes,edges} }, graphHash?, stats? }
//   meta = { taskName, device, mumuPort, timeoutMul, loopCount, note }
// 说明: 节点 dslLine 取 cfg.dslLine(由 dsl_to_flow mkNode 写入); 无则省略行注释。

(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) module.exports = factory();
  else root.ExportMd = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function esc(s) { return String(s == null ? "" : s); }

  // 单节点 → 近似 DSL 语句文本(仅用于导出阅读; 按键精灵风格, 与编辑器代码一致)
  function nodeToDsl(n) {
    var t = n.type, cfg = n.cfg || n.config || {}, op = cfg.op;  // UX-15: 兼容原生节点 config
    if (t === "start") return "start";
    if (t === "end") return "end";
    if (t === "sub_call") return "Call " + esc(cfg.sub) + "()";
    if (t === "break") return "Exit Do";
    if (t === "continue") return "Continue";
    if (t === "if_else") return "If " + esc(cfg.expr) + " Then";
    if (t === "loop_for")
      return "For " + esc(cfg.var || "i") + " = " + esc(cfg.from || 1) + " To " + esc(cfg.to || 10) + (cfg.step ? " Step " + cfg.step : "");
    if (t === "loop_while") return "While " + esc(cfg.expr);
    if (t === "loop_do_while") return "Do";
    if (t === "loop_for_each") return "For Each " + esc(cfg.var || "x") + " In " + esc(cfg.expr || "[]");
    if (t === "switch_case") return "Switch " + esc(cfg.expr);
    if (t === "print" || op === "log") {
      var _txt = esc(cfg.text);
      return (cfg.level === "warn" ? "TracePrint \"[警告] " + _txt.replace(/^"/, "").replace(/"$/, "") + "\"" : "TracePrint " + _txt);
    }
    if (op === "exit_script") return "Exit";
    if (op === "assign") return "Let " + esc(cfg.var) + " = " + esc(cfg.expr);
    if (op === "arr_push") return "Push " + esc(cfg.var) + ", " + esc(cfg.expr);
    if (op === "delay") return "Delay " + esc(cfg.ms != null ? cfg.ms : (cfg.msMax || 0));
    if (op === "tap") return "Tap " + esc(cfg.x) + ", " + esc(cfg.y);
    if (op === "tap_image") return "TapImage \"" + esc(cfg.image) + "\"" + (cfg.threshold != null && cfg.threshold !== 0.85 ? " threshold " + cfg.threshold : "");
    if (op === "swipe") return "Swipe " + esc(cfg.x1) + ", " + esc(cfg.y1) + ", " + esc(cfg.x2) + ", " + esc(cfg.y2);
    if (op === "key") return "KeyPress " + esc(cfg.key ? "\"" + cfg.key + "\"" : "");
    if (op === "input_text" || op === "type") return "SayString " + esc(cfg.text ? "\"" + cfg.text + "\"" : "");
    if (op === "wait_image") return "WaitImage \"" + esc(cfg.image || cfg.text || cfg.color) + "\"" + (cfg.timeout_ms ? " timeout " + Math.round(cfg.timeout_ms / 1000) + "s" : "");
    if (op === "wait_text") return "WaitText \"" + esc(cfg.image || cfg.text) + "\"";
    if (op === "wait_color") return "WaitColor \"" + esc(cfg.color) + "\"";
    if (op === "wait_gone") return "WaitGone \"" + esc(cfg.image) + "\"";
    if (op === "wait_key") return "WaitKey \"" + esc(cfg.key) + "\"";
    if (op === "screenshot") return "SnapShot \"" + esc(cfg.file || "") + "\"";
    if (op === "am_start") return "AppStart \"" + esc(cfg.pkg) + "\"";
    if (op === "am_stop") return "AppStop \"" + esc(cfg.pkg) + "\"";
    if (op === "emulator") return "Emulator " + esc(cfg.cmd);
    if (op === "read_file") return "ReadFile " + esc(cfg.file) + " -> " + esc(cfg.var);
    if (op === "match_image" || op === "match_color" || op === "match_multi_color") return "FindImage \"" + esc(cfg.image || cfg.colors) + "\"";
    if (op === "ocr_find" || op === "ocr_text" || op === "ocr_number" || op === "ocr_contains") return "Ocr \"" + esc(cfg.text) + "\"";
    if (op === "color_at") return "ColorAt " + esc(cfg.expr);
    if (op === "app_current") return "AppCurrent";
    // F-LOG-2 修复：call 动作节点导出时带子程序名（原来裸 call 丢名）
    if (op === "call") return "Call " + esc(cfg.sub || cfg.name || "") + "()";
    if (op) return esc(op) + (cfg.args ? " " + esc(cfg.args) : "");
    return esc(t);
  }

  function lineComment(n) {
    var L = n.cfg && n.cfg.dslLine;
    return (L != null && L > 0) ? "  <!-- L" + L + " -->" : "";
  }

  function emitGraph(g, indent) {
    if (!g || !g.nodes) return [];
    var out = [];
    g.nodes.forEach(function (n) {
      if (n.type === "start" || n.type === "end") return; // 起止节点不占语句行
      out.push("  ".repeat(indent) + nodeToDsl(n) + lineComment(n));
    });
    return out;
  }

  function collectVars(subgraphs) {
    var vars = [];
    Object.keys(subgraphs).forEach(function (k) {
      var g = subgraphs[k];
      if (!g || !g.nodes) return;
      g.nodes.forEach(function (n) {
        var op = (n.cfg || {}).op;
        if (op === "assign" || op === "arr_push")
          vars.push({ name: (n.cfg.var || "?"), val: (n.cfg.expr || ""), kind: op === "arr_push" ? "push" : "let" });
      });
    });
    // 去重(同名取首次)
    var seen = {}, uniq = [];
    vars.forEach(function (v) { if (!seen[v.name]) { seen[v.name] = 1; uniq.push(v); } });
    return uniq;
  }

  function buildMd(input, meta) {
    meta = meta || {};
    var flow = input || {};
    var graph = flow.graph || null;
    var subgraphs = flow.subgraphs || {};
    if (!graph && subgraphs.main) graph = subgraphs.main;
    var lines = [];

    // 1. 标题
    lines.push("# " + (meta.taskName || "autofw 工作流"));
    lines.push("");

    // 2. 执行参数(§3.7: 设备序列号 / MuMu 端口 / 超时倍率 / 循环次数)
    lines.push("## 执行参数");
    lines.push("");
    lines.push("| 参数 | 值 |");
    lines.push("|---|---|");
    lines.push("| 设备序列号 | " + (meta.device || "—") + " |");
    lines.push("| MuMu 端口 | " + (meta.mumuPort || "—") + " |");
    lines.push("| 超时倍率 | " + (meta.timeoutMul != null ? meta.timeoutMul : "—") + " |");
    lines.push("| 循环次数 | " + (meta.loopCount != null ? meta.loopCount : "—") + " |");
    lines.push("");

    // 3. 变量表
    lines.push("## 变量表");
    lines.push("");
    var vars = collectVars(subgraphs);
    if (!vars.length) lines.push("_（无变量声明）_");
    else {
      lines.push("| 变量 | 类型 | 初值 |");
      lines.push("|---|---|---|");
      vars.forEach(function (v) {
        lines.push("| " + v.name + " | " + v.kind + " | " + v.val + " |");
      });
    }
    lines.push("");

    // 4. 主流程
    lines.push("## 主流程");
    lines.push("");
    if (graph && graph.nodes && graph.nodes.length)
      emitGraph(graph, 0).forEach(function (l) { lines.push(l); });
    else lines.push("_（空）_");
    lines.push("");

    // 5. 子流程
    lines.push("## 子流程");
    lines.push("");
    var subs = Object.keys(subgraphs).filter(function (k) { return k.indexOf("sub:") === 0; });
    if (!subs.length) lines.push("_（无子流程）_");
    else subs.forEach(function (k) {
      lines.push("### " + k.slice(4));
      emitGraph(subgraphs[k], 1).forEach(function (l) { lines.push(l); });
      lines.push("");
    });

    // 6. 备注
    lines.push("## 备注");
    lines.push("");
    lines.push(meta.note || "_（无）_");
    lines.push("");
    return lines.join("\n");
  }

  return { buildMd: buildMd };
});
// UX-15 修复：同时暴露全局 buildMd 别名（与 window.ExportMd.buildMd 等价，兼容两种调用约定）
if (typeof window !== "undefined") { window.buildMd = (window.ExportMd || {}).buildMd; }


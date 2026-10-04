# -*- coding: utf-8 -*-
"""flow_engine.py - 图形化工作流：存储/版本、静态校验、FlowRunner 执行引擎。

数据模型（state/flows/<pkg>.json）:
{
  "pkg": str, "name": str, "version": int, "updated": str,
  "graph": {"nodes": [...], "edges": [...]},
  "subgraphs": {"<container_node_id>": {"nodes": [...], "edges": [...]}},
  "versions": [ {version, updated, graph, subgraphs}, ... ]   # 最近 20 版
}

节点类型:
  start / end / action / condition / loop_for / loop_while / loop_do_while
  - start: 顶层恰好 1 个；子图内也可有 1 个 start（表示循环体入口）
  - end: 顶层结束；子图内 end = 返回容器（未闭合循环 = 子图无 end）
  - action: config.action 动作原语（task:/adb_tap/am_start/wait/swipe/key/ocr_check）
  - condition: config.kind=match_image|ocr_contains|expr；出边 label=true/false
  - loop_*: 容器节点，subgraphs[id] 存子图；config.count / config.condition / config.max_iter

执行语义:
  - 普通节点: 执行后沿出边(首条)前进
  - condition: 判定后走 label=true/false 的边
  - loop_for: 重复执行子图 count 次（子图内 end 返回）
  - loop_while: 先判条件再执行子图
  - loop_do_while: 先执行子图一次再判条件（至少一次）
  - 防护: max_depth=32（容器嵌套）/ max_steps=10000（总动作步）/ 单循环 max_iter=1000
    （for 取 min(config.max_iter,1000)；while/do-while 同样受 max_iter 保护）

台账: state/flows/run_logs.json 追加 {id, pkg, started, finished, status,
  nodes:[{id,type,name,enter,exit,result}], events:[{t, node, msg}], summary}
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime as _dt

log = logging.getLogger("autofw.flow")  # 节点级进度写入 app.log（主控制台运行日志可见）
# 打印节点专用 logger：输出到 state/prints.log，前端「打印输出」面板独立展示
prints_log = logging.getLogger("autofw.prints")

# 嵌套深度上限 32 层 × 层内链长不受限（迭代式执行），预留递归栈余量
sys.setrecursionlimit(20000)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # autofw 根
FLOWS_DIR = os.path.join(BASE, "state", "flows")
LOGS_FILE = os.path.join(BASE, "state", "flows", "run_logs.json")
MAX_VERSION_KEEP = 20
DEFAULT_MAX_ITER = 1000
MIN_POLL_MS = 200  # 空循环体（等待直到条件满足）的最小轮询间隔(ms)，避免忙等占满 CPU
MAX_DEPTH = 32
MAX_STEPS = 10000

_lock = threading.Lock()

NODE_TYPES = ("start", "end", "action", "condition", "if_else", "else_if", "switch_case",
              "loop_for", "loop_while", "loop_do_while", "print", "break")
LOOP_TYPES = ("loop_for", "loop_while", "loop_do_while")
ACTION_KINDS = ("task", "adb_tap", "am_start", "wait", "swipe", "key", "ocr_check")
CONDITION_KINDS = ("match_image", "region_match", "ocr_contains", "expr")
# 双分支判定节点（condition / if_else）：执行与校验规则一致，按 true/false 出边分流
BRANCH_TYPES = ("condition", "if_else", "else_if")

def _to_bool(v):
    """布尔归一化：True/False 原样；字符串按 true/1/yes 解析（防 "false" 被 bool() 判 True）；
    数字非零为真；解析失败抛 ValueError（调用方转成校验/运行错误）。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off", ""):
            return False
    raise ValueError("无法解析为布尔值: %r" % (v,))


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


FLOW_FIELDS = ("pkg", "name", "version", "updated", "graph", "subgraphs")


# ---------- 存储 ----------

import re as _re


def _flow_path(pkg):
    # 文件名净化：Windows 非法字符与路径分隔全部替换
    safe = _re.sub(r"[\\/:*?\"<>|\s]+", "_", pkg).strip("._") or "default"
    return os.path.join(FLOWS_DIR, safe + ".json")


def list_flows():
    try:
        names = [f[:-5] for f in os.listdir(FLOWS_DIR) if f.endswith(".json") and f != "run_logs.json"]
    except OSError:
        return []
    out = []
    for n in sorted(names):
        try:
            with open(_flow_path(n), "r", encoding="utf-8") as f:
                d = json.load(f)
            out.append({"pkg": d.get("pkg", n), "name": d.get("name", n),
                        "version": d.get("version", 0),
                        "updated": d.get("updated", "")})
        except (OSError, ValueError):
            pass
    return out


def load_flow(pkg):
    try:
        with open(_flow_path(pkg), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_flow(pkg, payload):
    """保存工作流。结构变化时版本号自增，旧版入 versions（保留最近 20 版）。"""
    graph = payload.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return {"ok": False, "error": "graph 结构无效（缺 nodes）"}
    subgraphs = payload.get("subgraphs") or {}
    name = (payload.get("name") or pkg).strip() or pkg
    with _lock:
        old = load_flow(pkg)
        version = (old.get("version") or 0) + 1 if old else 1
        rec = {"pkg": pkg, "name": name, "version": version,
               "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "graph": graph, "subgraphs": subgraphs,
               "versions": []}
        if old:
            hist = old.get("versions") or []
            hist.append({"version": old.get("version", 0),
                         "updated": old.get("updated", ""),
                         "graph": old.get("graph"), "subgraphs": old.get("subgraphs")})
            rec["versions"] = hist[-MAX_VERSION_KEEP:]
        os.makedirs(FLOWS_DIR, exist_ok=True)
        try:
            with open(_flow_path(pkg), "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
        except OSError as e:
            return {"ok": False, "error": "保存失败（磁盘/权限）: %s" % e}
        return {"ok": True, "version": version, "msg": "已保存 v%d" % version}


# ---------- 静态校验 ----------

def _nodes_of(flow):
    return flow.get("graph", {}).get("nodes", []), flow.get("graph", {}).get("edges", [])


def _reachable_ends(nodes, edges, entry_id):
    """从 entry 出发 BFS，能否到达 end 节点。返回 (可达, 路径节点数)。"""
    edge_map = {}
    for e in edges:
        edge_map.setdefault(e["from"], []).append(e["to"])
    seen = set()
    stack = [entry_id]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        for t in edge_map.get(nid, []):
            stack.append(t)
    ends = [n for n in nodes if n.get("type") == "end"]
    return any(e["id"] in seen for e in ends), len(seen)


def validate_flow(flow):
    """校验工作流（递归：容器子图内部同样全量检查）。返回问题列表；空列表 = 合法。"""
    issues = []
    graph = flow.get("graph", {})
    subgraphs = flow.get("subgraphs") or {}
    _validate_graph(graph.get("nodes", []), graph.get("edges", []),
                    subgraphs, issues, prefix="", depth=0)
    return issues


def _validate_graph(nodes, edges, subgraphs, issues, prefix, depth):
    """单层图校验（递归调用子图）。prefix 用于定位（如 循环A > 循环B > ）。"""
    if depth > MAX_DEPTH:
        issues.append({"node": None, "level": "error",
                       "msg": "%s嵌套超过 %d 层上限" % (prefix, MAX_DEPTH)})
        return
    node_ids = {n["id"] for n in nodes}
    seen_ids = set()
    dup_ids = set()
    for n in nodes:
        if n["id"] in seen_ids:
            dup_ids.add(n["id"])
        seen_ids.add(n["id"])
    for did in dup_ids:
        issues.append({"node": did, "level": "error", "msg": "%s重复的节点 id：%s" % (prefix, did)})
    starts = [n for n in nodes if n.get("type") == "start"]
    ends = [n for n in nodes if n.get("type") == "end"]

    if not nodes and depth == 0:
        issues.append({"node": None, "level": "error", "msg": "画布为空：请添加开始节点"})
        return
    if len(starts) != 1:
        issues.append({"node": starts[0]["id"] if starts else None, "level": "error",
                       "msg": "%s必须有且仅有 1 个「开始」节点（当前 %d 个）" % (prefix, len(starts))})
    for e in edges:
        if e["from"] not in node_ids or e["to"] not in node_ids:
            issues.append({"node": e.get("from"), "level": "error",
                           "msg": "%s连线「%s→%s」指向不存在的节点" % (prefix, e.get("from"), e.get("to"))})
    for n in nodes:
        t = n.get("type")
        cfg = n.get("config") or {}
        if t not in NODE_TYPES:
            issues.append({"node": n["id"], "level": "error",
                           "msg": "%s未知节点类型「%s」" % (prefix, t)})
            continue
        loc = prefix + "「%s」" % n.get("name")
        # 配置缺失 / 类型错误
        if t == "break" or (t == "action" and cfg.get("op") == "break"):
            pass  # break：语义合法；是否在循环内由引擎运行时判定
        elif t == "action" and not cfg.get("action"):
            issues.append({"node": n["id"], "level": "warning", "msg": "%s动作节点未配置动作" % loc})
        if t == "print" and not str(cfg.get("text") or "").strip():
            issues.append({"node": n["id"], "level": "warning", "msg": "%s打印节点未填写打印内容" % loc})
        if t in BRANCH_TYPES:
            kind = cfg.get("kind")
            if kind not in CONDITION_KINDS:
                issues.append({"node": n["id"], "level": "error",
                               "msg": "%s未选择判定方式（全屏匹配/区域内匹配/OCR/表达式）" % loc})
            elif kind == "match_image" and not cfg.get("image"):
                issues.append({"node": n["id"], "level": "error", "msg": "%s未选择匹配图像" % loc})
            elif kind == "region_match":
                if not cfg.get("region_match") or not cfg.get("target_crop_b64"):
                    issues.append({"node": n["id"], "level": "error",
                                   "msg": "%s区域内匹配未完整配置（需圈选区域和目标图像）" % loc})
            elif kind == "ocr_contains" and not cfg.get("text"):
                issues.append({"node": n["id"], "level": "error", "msg": "%s未填写目标文字" % loc})
        if t == "switch_case":
            if not (cfg.get("cases") or []):
                issues.append({"node": n["id"], "level": "error", "msg": "%s未配置分支列表（cases 为空）" % loc})
            elif not str(cfg.get("expr") or "").strip():
                issues.append({"node": n["id"], "level": "warning", "msg": "%s未填写匹配表达式（expr）" % loc})
        if t == "loop_for":
            count = cfg.get("count")
            if count is None or count == "":
                issues.append({"node": n["id"], "level": "error", "msg": "%s未配置循环次数" % loc})
            else:
                try:
                    if int(count) < 0:
                        issues.append({"node": n["id"], "level": "warning",
                                       "msg": "%s循环次数为负数，将按 0 次执行" % loc})
                except (TypeError, ValueError):
                    issues.append({"node": n["id"], "level": "error",
                                   "msg": "%s循环次数必须是数字（当前 %r）" % (loc, count)})
        if t in ("loop_while", "loop_do_while"):
            cond = cfg.get("condition")
            if cond is not None:
                try:
                    _to_bool(cond)
                except ValueError:
                    issues.append({"node": n["id"], "level": "error",
                                   "msg": "%s退出条件无法解析为布尔值（%r）" % (loc, cond)})
            elif cfg.get("kind") == "region_match":
                if not cfg.get("region_match") or not cfg.get("target_crop_b64"):
                    issues.append({"node": n["id"], "level": "error",
                                   "msg": "%s区域内匹配退出判定未完整配置（需圈选区域和目标图像）" % loc})
            elif not cfg.get("image") and not cfg.get("text"):
                issues.append({"node": n["id"], "level": "error",
                               "msg": "%s未配置退出条件（布尔/匹配图/区域内匹配/OCR 至少其一）" % loc})
        # 出边检查
        outs = [e for e in edges if e["from"] == n["id"]]
        if t in BRANCH_TYPES:
            labels = sorted(e.get("label", "") for e in outs)
            if len(outs) != 2 or labels != ["false", "true"]:
                # 缺分支出边在运行时是确定中止（FlowRunner RunAborted「缺少 true/false 出边」），
                # 故保持 error 级；文案须写明后果与修法（该分支无需动作时→出边连汇合点或结束节点）。
                # ⚠️ 一致性备忘：switch_case 缺分支目前仅 warning 但运行同样中止，标准不一致
                #   已登记 docs/handover/04-todo.md P2，对齐方案待定（2026-09-29 评审）。
                miss = "/".join(lb for lb in ("true", "false") if lb not in labels) or "true/false"
                issues.append({"node": n["id"], "level": "error",
                               "msg": "%s出边不符合要求：需要 true / false 两条出边各一条（当前 %d 条，缺 %s）；"
                                      "缺边时运行到该分支将中止整个流程，该分支无需动作时请把出边连到汇合点或结束节点"
                               % (loc, len(outs), miss)})
        elif t == "switch_case":
            # 多分支：允许 N 条出边（每分支至多 1 条）；缺分支告警
            # ⚠️ 一致性备忘（2026-09-29）：缺分支且无 default 时运行同样 RunAborted（FlowRunner switch 段），
            # 与双分支缺边=error 的标准不一致；已登记 docs/handover/04-todo.md P2，暂维持现状。
            labels = [e.get("label", "") for e in outs]
            seen = set()
            for lb in labels:
                if lb in seen:
                    issues.append({"node": n["id"], "level": "warning",
                                   "msg": "%s分支「%s」有多条出边（仅第一条生效）" % (loc, lb)})
                seen.add(lb)
            for c in (cfg.get("cases") or []):
                if str(c) not in labels and not cfg.get("default"):
                    issues.append({"node": n["id"], "level": "warning",
                                   "msg": "%s分支「%s」未连接出边（且无 default）" % (loc, c)})
        elif t == "end":
            if outs:
                issues.append({"node": n["id"], "level": "warning", "msg": "%s结束节点不应有出边" % loc})
        elif len(outs) > 1:
            issues.append({"node": n["id"], "level": "warning",
                           "msg": "%s有 %d 条出边（仅第一条生效）" % (loc, len(outs))})
        elif not outs:
            issues.append({"node": n["id"], "level": "warning",
                           "msg": "%s节点没有出边（流程会在此终止）" % loc})
        # 孤立节点
        has_in = any(e["to"] == n["id"] for e in edges)
        if t != "start" and not has_in and not outs:
            issues.append({"node": n["id"], "level": "warning", "msg": "%s孤立节点：既无入边也无出边" % loc})
        # 容器完整性 + 递归子图校验
        # 空循环体（无节点）视为合法的「等待直到条件满足」纯轮询循环，
        # 不再强制要求循环体（与 while 的退出条件/迭代延时配合即可）。
        if t in LOOP_TYPES:
            sub = subgraphs.get(n["id"])
            if sub and sub.get("nodes"):
                sub_starts = [x for x in sub["nodes"] if x.get("type") == "start"]
                sub_ends = [x for x in sub["nodes"] if x.get("type") == "end"]
                if not sub_starts:
                    issues.append({"node": n["id"], "level": "error",
                                   "msg": "%s循环体缺少「开始」节点（循环入口）" % loc})
                if not sub_ends:
                    issues.append({"node": n["id"], "level": "error",
                                   "msg": "%s未闭合循环：循环体缺少「结束」节点（返回出口）" % loc})
                elif sub_starts:
                    entry = sub_starts[0]["id"]
                    ok, _ = _reachable_ends(sub["nodes"], sub.get("edges", []), entry)
                    if not ok:
                        issues.append({"node": n["id"], "level": "error",
                                       "msg": "%s死循环风险：循环体「结束」节点不可达（条件永不退出？）" % loc})
                # 递归：子图内部全量校验
                _validate_graph(sub.get("nodes", []), sub.get("edges", []),
                                subgraphs, issues, prefix=prefix + n.get("name") + " > ",
                                depth=depth + 1)
    # 入口可达性（从 start BFS 能否到 end）
    if starts and ends:
        ok, _ = _reachable_ends(nodes, edges, starts[0]["id"])
        if not ok:
            issues.append({"node": starts[0]["id"], "level": "error",
                           "msg": "%s流程无法到达「结束」节点（连线可能断裂）" % prefix})


# ---------- 执行引擎 ----------

class FlowExecutor:
    """动作执行器抽象。真实实现走 ADB；测试用 MockExecutor 替换。"""

    def execute_action(self, node, ctx):
        """执行 action 节点，返回 True/False。ctx 含 device、pkg、templates 目录。"""
        raise NotImplementedError

    def evaluate_condition(self, node, ctx):
        """判定 condition 节点，返回 True/False。"""
        raise NotImplementedError

    def evaluate_switch(self, node, ctx):
        """判定 switch_case 节点：按 expr 求值，匹配 config.cases 返回分支值；
        未命中且有 default 返回 "default"；否则返回 None（调用方按无匹配处理）。
        expr 求值：尝试 ast.literal_eval（支持 "case1"/2/True 等字面量与简单表达式），
        失败则按原始字符串（去引号）处理；当前无变量作用域（与 condition.expr 一致）。"""
        import ast as _ast
        cfg = node.get("config") or {}
        expr = (cfg.get("expr") or "").strip()
        if not expr:
            return None
        raw = None
        try:
            raw = _ast.literal_eval(expr)
        except (ValueError, SyntaxError):
            s = expr.strip()
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                s = s[1:-1]
            raw = s
        cases = [str(c) for c in (cfg.get("cases") or [])]
        for c in cases:
            if str(raw) == str(c):
                return str(c)
        if cfg.get("default"):
            return "default"
        return None


class MockExecutor(FlowExecutor):
    """单测用：按 config 返回预设结果，不碰设备。"""

    def __init__(self, action_ok=True, cond_result=True, trace=None):
        self.action_ok = action_ok
        self.cond_result = cond_result
        self.trace = trace if trace is not None else []
        self.last_error = ""

    def execute_action(self, node, ctx):
        self.trace.append(("action", node["id"], node.get("name")))
        return self.action_ok

    def evaluate_condition(self, node, ctx):
        cfg = node.get("config") or {}
        try:
            r = cfg.get("mock_result", self.cond_result)
            r = _to_bool(r)
        except ValueError as e:
            self.last_error = str(e)
            raise
        self.trace.append(("cond", node["id"], r))
        return bool(r)


class RunAborted(Exception):
    pass


class LoopBreak(Exception):
    """break 节点 / action.op=break：跳出最近一层循环体。"""
    pass


class FlowRunner:
    """按图执行工作流。容器节点递归执行子图。防护：深度/步数/迭代上限。"""

    def __init__(self, executor, flow, max_depth=MAX_DEPTH, max_steps=MAX_STEPS,
                 default_max_iter=DEFAULT_MAX_ITER):
        self.executor = executor
        self.flow = flow
        self.subgraphs = flow.get("subgraphs") or {}
        self.max_depth = max_depth
        self.max_steps = max_steps
        self.default_max_iter = default_max_iter
        self.events = []
        self.node_recs = []
        self.steps = 0
        self.aborted = False
        self.paused = False
        self._pause_ev = threading.Event()
        self._pause_ev.set()

    # --- 控制 ---
    def pause(self):
        self.paused = True
        self._pause_ev.clear()

    def resume(self):
        self.paused = False
        self._pause_ev.set()

    def stop(self):
        self.aborted = True
        self._pause_ev.set()

    def _checkpoint(self):
        # 先查终止标志（防 stop 后 pause 的竞态：stop 置位后 event 被 clear 也不得阻塞）
        if self.aborted:
            raise RunAborted("已终止")
        self._pause_ev.wait()
        if self.aborted:
            raise RunAborted("已终止")

    def _guard(self, depth, loop_count, node, max_iter=None):
        self.steps += 1
        if self.steps > self.max_steps:
            self.events.append({"t": _ts(), "node": node.get("id"),
                                "msg": "风险：总步骤超过 %d，触发最大迭代保护，已中断" % self.max_steps})
            raise RunAborted("死循环防护：总步骤超限（%d 步）" % self.max_steps)
        if depth > self.max_depth:
            self.events.append({"t": _ts(), "node": node.get("id"),
                                "msg": "风险：嵌套深度超过 %d，已中断" % self.max_depth})
            raise RunAborted("嵌套深度超限（>%d 层）" % self.max_depth)
        limit = max_iter if max_iter is not None else self.default_max_iter
        if loop_count >= limit:
            self.events.append({"t": _ts(), "node": node.get("id"),
                                "msg": "风险：单循环迭代达到上限 %d 次（无退出条件？），已中断" % limit})
            raise RunAborted("死循环防护：迭代次数超限（%d 次）" % limit)

    def _enter(self, node, extra=None):
        rec = {"id": node["id"], "type": node.get("type"), "name": node.get("name"),
               "enter": _ts(), "exit": None, "result": None}
        if extra:
            rec.update(extra)
        self.node_recs.append(rec)
        self.events.append({"t": rec["enter"], "node": node["id"],
                            "msg": "进入「%s」(%s)" % (node.get("name"), node.get("type"))})
        log.info("[flow] 进入节点「%s」(%s)", node.get("name"), node.get("type"))
        return rec

    def _exit(self, rec, result, msg=""):
        rec["exit"] = _ts()
        rec["result"] = result
        self.events.append({"t": rec["exit"], "node": rec["id"],
                            "msg": "离开「%s」→ %s%s" % (rec.get("name"), result, (" " + msg) if msg else "")})
        log.info("[flow] 离开节点「%s」→ %s", rec.get("name"), result)

    def _next_edge(self, edges, nid, cond_result=None, label=None):
        outs = [e for e in edges if e["from"] == nid]
        if not outs:
            return None
        if label is not None:
            for e in outs:
                if e.get("label") == label:
                    return e
            for e in outs:  # 兜底：无标签边
                if not e.get("label"):
                    return e
            return None
        if cond_result is not None:
            for e in outs:
                if e.get("label") == str(bool(cond_result)).lower():
                    return e
            for e in outs:  # 兜底：没标签的边
                if not e.get("label"):
                    return e
            return None
        return outs[0]

    # --- 执行 ---
    def _locate_node(self, node_id):
        """定位起始节点：先查主画布，再查各子图（循环体 / 判断分支体 / switch 分支体）。

        返回 (node, sub, depth, where)；sub 为 None 表示节点在主画布。
        sub 形如 {"nodes": [...], "edges": [...]}，depth 为容器嵌套层数（喂给 _guard）。

        说明：节点 id 全局唯一（n_<时间戳36><随机>），故直接遍历子图定位无歧义，
        无需前端额外传上下文路径——这样也不必改动 server.py 的请求协议。
        """
        graph = self.flow.get("graph", {})
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        if node_id in nodes:
            return (nodes[node_id], None, 0, "主画布")
        for key in (self.subgraphs or {}):
            sub = (self.subgraphs or {}).get(key)
            if not isinstance(sub, dict):
                continue
            sub_nodes = sub.get("nodes") or []
            for n in sub_nodes:
                if isinstance(n, dict) and n.get("id") == node_id:
                    where = "子图 %s" % key
                    return (n, {"nodes": sub_nodes, "edges": sub.get("edges", [])}, 1, where)
        return None

    def run(self, start_node_id=None):
        """顶层入口。返回 summary dict。
        start_node_id 不为 None 时从该节点开始执行（调试用：从选中节点运行），
        否则沿用「开始」节点。

        起点可以来自主画布，也可以来自循环体 / 分支体子图内。
        后者按「A 方案」执行：只单程跑该子图内从该节点起的这一段，
        不重入外层循环（不做条件重判、不计迭代次数、不引入 max_iter 上下文）——
        因为循环迭代上下文（iters/loop_count/max_iter）由父循环节点持有，
        脱离父循环单独续跑时这些语义本就未定义。
        """
        self.started = _ts()
        try:
            graph = self.flow.get("graph", {})
            nodes = {n["id"]: n for n in graph.get("nodes", [])}
            if start_node_id:
                loc = self._locate_node(start_node_id)
                if not loc:
                    raise ValueError("未找到起始节点：%s（主画布与各循环体/分支体子图均未找到）" % start_node_id)
                start, sub, depth, where = loc
                if sub is not None:
                    sub_nodes = {n["id"]: n for n in sub["nodes"]}
                    self.events.append({"t": _ts(), "node": start_node_id,
                                        "msg": "调试运行：从%s内节点「%s」单程开始（不重入外层循环）"
                                               % (where, start.get("name") or start_node_id)})
                    self._walk(start, sub, depth=depth, loop_count=0, nodes=sub_nodes)
                    return self._summary("ok")
            else:
                starts = [n for n in graph.get("nodes", []) if n.get("type") == "start"]
                if not starts:
                    raise ValueError("缺少「开始」节点")
                start = starts[0]
            self._walk(start, graph, depth=0, nodes=nodes)
            return self._summary("ok")
        except RunAborted as e:
            return self._summary("aborted", str(e))
        except LoopBreak:
            self.events.append({"t": _ts(), "node": None,
                                "msg": "break 出现在循环外（无所属循环），流程在此结束"})
            log.warning("[flow] break 不在循环内，流程结束")
            return self._summary("aborted", "break 不在循环内")
        except Exception as e:  # noqa: BLE001
            self.events.append({"t": _ts(), "node": None, "msg": "异常：%s" % e})
            return self._summary("error", str(e))
        finally:
            self.finished = _ts()

    def _summary(self, status, note=""):
        log.info("[flow] 执行结束：%s%s（%d 步）", status, (" - " + note) if note else "", self.steps)
        return {"status": status, "note": note, "steps": self.steps,
                "nodes": self.node_recs, "events": self.events,
                "node_count": len(self.node_recs),
                "started": getattr(self, "started", _ts()),
                "finished": getattr(self, "finished", _ts())}

    def _walk(self, node, graph, depth, loop_count=0, nodes=None):
        """执行节点链：迭代式沿边前进；仅容器嵌套递归（深度 ≤ max_depth）。"""
        if nodes is None:
            nodes = {n["id"]: n for n in graph.get("nodes", [])}
        edges = graph.get("edges", [])
        while True:
            self._checkpoint()
            self._guard(depth, loop_count, node)
            t = node.get("type")
            rec = self._enter(node)

            if t == "end":
                self._exit(rec, "ok")
                return

            _cfg = node.get("config") or {}
            if t == "break" or (t == "action" and _cfg.get("op") == "break"):
                self._exit(rec, "ok", "跳出循环")
                self.events.append({"t": _ts(), "node": node.get("id"),
                                    "msg": "break：跳出当前循环"})
                log.info("[flow] break 节点「%s」→ 跳出循环", node.get("name"))
                raise LoopBreak()

            if t == "action":
                rec["action"] = (node.get("config") or {}).get("action", "")
                ok = self.executor.execute_action(node, {"pkg": self.flow.get("pkg")})
                err = getattr(self.executor, "last_error", "")
                if ok and err and "无变化" in err:
                    # 47 轮：tap 生效自检警告——不中断流程，写事件日志（前端运行日志可见）
                    tv = getattr(self.executor, "tap_verify", None)
                    detail = err + (("（差异均值 %s）" % tv["mean_abs"]) if tv and tv.get("mean_abs") is not None else "")
                    self.events.append({"t": _ts(), "node": node.get("id"), "msg": "⚠ " + detail})
                    log.warning("[%s] %s", node.get("name"), detail)
                if not ok:
                    self._exit(rec, "fail", err or "动作执行失败")
                    raise RunAborted("动作失败：%s%s" % (node.get("name"), ("（" + err + "）") if err else ""))
                self._exit(rec, "ok")
                nxt = self._next_edge(edges, node["id"])
                if nxt is None:
                    return
                node = nodes[nxt["to"]]
                continue

            if t == "print":
                # 打印节点：输出到专用 prints logger（state/prints.log，前端「打印输出」面板独立展示）
                # 同时保留 app.log 一行，便于运行日志追溯。
                text = str((node.get("config") or {}).get("text") or "")
                prints_log.info("%s", text)
                log.info("[print] %s", text)
                self._exit(rec, "ok", text)
                nxt = self._next_edge(edges, node["id"])
                if nxt is None:
                    return
                node = nodes[nxt["to"]]
                continue

            if t in BRANCH_TYPES:
                try:
                    res = self.executor.evaluate_condition(node, {"pkg": self.flow.get("pkg")})
                except Exception as e:  # 判定异常不得静默降级为 false（P0-5）
                    self._exit(rec, "fail", str(e))
                    raise RunAborted("条件判定失败：%s（%s）" % (node.get("name"), e))
                self._exit(rec, "ok" if res else "false",
                           "判定依据=%s" % self._cond_basis(node))
                # 分支体子图模式（可选，向后兼容）：若配置了 TRUE/FALSE 分支体子图且非空，
                # 先执行分支体，再继续走 T/F 出边到分支后续节点；无分支体子图则保持旧的连线行为。
                branch_key = node["id"] + (":true" if res else ":false")
                sub = self.subgraphs.get(branch_key)
                if sub and sub.get("nodes"):
                    self._run_sub(sub, depth + 1, loop_count=0, max_iter=None)
                nxt = self._next_edge(edges, node["id"], res)
                if nxt is None:
                    raise RunAborted("判定节点「%s」缺少 %s 出边（请连接 %s 分支）"
                                     % (node.get("name"), "true" if res else "false",
                                        "TRUE" if res else "FALSE"))
                node = nodes[nxt["to"]]
                continue

            if t == "switch_case":
                val = self.executor.evaluate_switch(node, {"pkg": self.flow.get("pkg")})
                self._exit(rec, "ok", "switch 命中=%s" % (val if val is not None else "无匹配"))
                if val is None:
                    raise RunAborted("分支节点「%s」无匹配分支且未配置 default（请连接 default 分支或检查 expr/cases）"
                                     % node.get("name"))
                nxt = self._next_edge(edges, node["id"], label=val)
                if nxt is None:
                    raise RunAborted("分支节点「%s」缺少 %s 出边（请连接该分支）"
                                     % (node.get("name"), val))
                node = nodes[nxt["to"]]
                continue

            if t in LOOP_TYPES:
                sub = self.subgraphs.get(node["id"])
                empty_body = not sub or not sub.get("nodes")
                cfg = node.get("config") or {}
                max_iter = min(_to_int(cfg.get("max_iter"), self.default_max_iter) or self.default_max_iter,
                               self.default_max_iter)
                iters = 0
                # 空循环体（无节点）= “等待直到条件满足”的纯轮询循环：每轮仅判定条件 + 迭代延时。
                # 迭代延时缺省时给一个最小轮询间隔(MIN_POLL_MS)，避免忙等占满 CPU。
                if empty_body and _to_int(cfg.get("delay_ms"), 0) < MIN_POLL_MS:
                    cfg = dict(cfg)
                    cfg["delay_ms"] = MIN_POLL_MS
                if t == "loop_for":
                    count = max(0, _to_int(cfg.get("count"), 0))
                    limit = min(count, max_iter)
                    while iters < limit:
                        iters += 1
                        self.events.append({"t": _ts(), "node": node["id"],
                                            "msg": "for 第 %d/%d 次" % (iters, limit)})
                        if iters == 1 or iters == limit or iters % 50 == 0:  # 节流：长循环不刷屏
                            log.info("[flow] 循环「%s」第 %d/%d 次", node.get("name"), iters, limit)
                        if not empty_body:
                            try:
                                self._run_sub(sub, depth + 1, loop_count=iters, max_iter=max_iter)
                            except LoopBreak:
                                self.events.append({"t": _ts(), "node": node["id"],
                                                    "msg": "for 循环被 break 跳出（第 %d 次）" % iters})
                                log.info("[flow] 循环「%s」被 break 跳出", node.get("name"))
                                break
                        self._checkpoint()
                        self._iter_delay(cfg)
                elif t == "loop_while":
                    # invert=True：把配置的条件当作“退出条件”（条件满足即退出），
                    # 即 while not 条件 —— 实现“等到某图/文字出现再退出”。默认 False 保持向后兼容。
                    invert = _to_bool(cfg.get("invert", False))
                    while self._loop_test(node, invert):
                        iters += 1
                        if not empty_body:
                            try:
                                self._run_sub(sub, depth + 1, loop_count=iters, max_iter=max_iter)
                            except LoopBreak:
                                self.events.append({"t": _ts(), "node": node["id"],
                                                    "msg": "while 循环被 break 跳出（第 %d 次）" % iters})
                                log.info("[flow] 循环「%s」被 break 跳出", node.get("name"))
                                break
                        self._checkpoint()
                        self._iter_delay(cfg)
                        if iters >= max_iter:
                            self._guard(depth, iters, node, max_iter=max_iter)
                else:  # do-while：至少执行一次
                    invert = _to_bool(cfg.get("invert", False))
                    while True:
                        iters += 1
                        if not empty_body:
                            try:
                                self._run_sub(sub, depth + 1, loop_count=iters, max_iter=max_iter)
                            except LoopBreak:
                                self.events.append({"t": _ts(), "node": node["id"],
                                                    "msg": "do-while 循环被 break 跳出（第 %d 次）" % iters})
                                log.info("[flow] 循环「%s」被 break 跳出", node.get("name"))
                                break
                        self._checkpoint()
                        self._iter_delay(cfg)
                        if iters >= max_iter:
                            self._guard(depth, iters, node, max_iter=max_iter)
                        if not self._loop_test(node, invert):
                            break
                self._exit(rec, "ok", "共 %d 次" % iters)
                nxt = self._next_edge(edges, node["id"])
                if nxt is None:
                    return
                node = nodes[nxt["to"]]
                continue

            if t == "start":
                self._exit(rec, "ok")
                nxt = self._next_edge(edges, node["id"])
                if nxt is None:
                    return
                node = nodes[nxt["to"]]
                continue

            self._exit(rec, "fail", "未知类型")
            raise RunAborted("未知节点类型：%s" % t)

    def _loop_cond(self, node):
        """循环退出条件：condition 布尔/字符串 > 匹配图像/OCR（自动补 kind）>
        兜底交 executor 判定。"""
        cfg = dict(node.get("config") or {})
        if cfg.get("condition") is not None:
            try:
                return _to_bool(cfg["condition"])
            except ValueError as e:
                raise RunAborted("循环退出条件解析失败：%s（%s）" % (node.get("name"), e))
        # 仅有 image/text 时补 kind（评审 P1：否则真实执行器落入 expr 分支判定失效）
        if cfg.get("image") and not cfg.get("kind"):
            cfg["kind"] = "match_image"
        if cfg.get("text") and not cfg.get("kind"):
            cfg["kind"] = "ocr_contains"
        return self.executor.evaluate_condition(
            {"id": node["id"] + "_cond", "type": "condition",
             "name": node.get("name") + "-条件", "config": cfg},
            {"pkg": self.flow.get("pkg")})

    def _loop_test(self, node, invert):
        """计算 while 循环的“继续条件”。invert=True 时取反，使配置的条件成为“退出条件”
        （即 while not 条件），用于实现“等到某图/文字出现再退出”的纯等待语义。"""
        c = self._loop_cond(node)
        # 匹配分数可观测（08-29）：判定节流 60s 记一条，运行日志直接可见各方法得分，
        # “识别不到指定图像”时无需取证即可定位（画面变化→分数低 / 配置缺损→抛错）
        ms = getattr(self.executor, "match_scores", None)
        now = time.time()
        if ms and now - getattr(self, "_last_match_log", 0.0) >= 60.0:
            self._last_match_log = now
            pretty = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in ms.items()}
            log.info("[flow] 循环「%s」匹配判定=%s 分数=%s（阈值 %s%%）", node.get("name"),
                     bool(c), pretty, (node.get("config") or {}).get("match_threshold", 80))
        return (not c) if invert else c

    def _cond_basis(self, node):
        cfg = node.get("config") or {}
        if cfg.get("kind") == "match_image":
            return "匹配图像 %s" % cfg.get("image")
        if cfg.get("kind") == "region_match":
            return "区域匹配图像（阈值 %s%%）" % cfg.get("match_threshold", 80)
        if cfg.get("kind") == "ocr_contains":
            return "OCR 包含「%s」" % cfg.get("text")
        return "表达式 %s" % cfg.get("expr", cfg.get("condition", "mock"))

    def _iter_delay(self, cfg):
        """循环迭代等待延时（可中断 sleep）：每 100ms 检查一次终止/暂停标志。"""
        delay = _to_int(cfg.get("delay_ms"), 0)
        if delay <= 0:
            return
        end = time.time() + delay / 1000.0
        while time.time() < end:
            self._checkpoint()
            time.sleep(min(0.1, max(0.01, end - time.time())))

    def _run_sub(self, sub, depth, loop_count, max_iter=None):
        sub_nodes = sub.get("nodes", []) if isinstance(sub, dict) else []
        if not sub_nodes:
            return  # 空循环体：纯轮询（等待直到条件满足），无节点可执行
        nodes = {n["id"]: n for n in sub_nodes}
        starts = [n for n in sub_nodes if n.get("type") == "start"]
        if not starts:
            raise RunAborted("循环体缺少「开始」节点")
        self._walk(starts[0], {"nodes": sub_nodes, "edges": sub.get("edges", [])},
                   depth, loop_count, nodes)


def graph_nodes(graph):
    return {n["id"]: n for n in graph.get("nodes", [])}


def _ts():
    return _dt.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# ---------- 运行台账 ----------

def save_run_log(pkg, summary):
    with _lock:
        try:
            with open(LOGS_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
            if not isinstance(logs, list):
                logs = []
        except (OSError, ValueError):
            logs = []
        rec = {"id": "run_%s" % _dt.now().strftime("%Y%m%d_%H%M%S_%f"),
               "pkg": pkg, "started": summary.get("started") or _ts(),
               "finished": summary.get("finished") or _ts(), "status": summary.get("status"),
               "note": summary.get("note", ""), "steps": summary.get("steps"),
               "nodes": list(summary.get("nodes", [])),
               "events": list(summary.get("events", []))[-200:]}
        logs.append(rec)
        logs = logs[-100:]
        os.makedirs(FLOWS_DIR, exist_ok=True)
        try:
            with open(LOGS_FILE, "w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return rec["id"]


def list_run_logs(pkg=None, limit=50):
    try:
        with open(LOGS_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    except (OSError, ValueError):
        return []
    if pkg:
        logs = [x for x in logs if x.get("pkg") == pkg]
    return logs[-limit:][::-1]

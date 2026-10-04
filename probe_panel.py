# -*- coding: utf-8 -*-
"""start.bat 端口探活：确认 8765 上跑的是不是真正的 autofw 面板。

输出（stdout，单行）：
  AUTOFW  —— 有 HTTP 响应且页面含 autofw 特征（真面板）
  OTHER   —— 有 HTTP 响应但不是 autofw（假占用，如别的程序占了 8765）
  NOHTTP  —— 端口被占用但无 HTTP 响应（非 Web 服务占用）

用于替代 start.bat 里"端口 busy 就认为面板已在运行"的粗判，
避免 2026-09-09 那种「被别的进程占端口 → 跳过启动 → 浏览器打不开」的误判。
"""
import urllib.request

URL = "http://127.0.0.1:8765/"
try:
    r = urllib.request.urlopen(URL, timeout=4)
    body = r.read(16384).decode("utf-8", "ignore").lower()
    print("AUTOFW" if "autofw" in body else "OTHER")
except Exception:
    print("NOHTTP")

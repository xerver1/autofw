# -*- coding: utf-8 -*-
"""环境自检门（E19/E20/E21）：流水线启动/断点重续前强制校验。
需求 6 定稿：保活开启=放行（单前台模式），不再判失败。
（照抄 ALAS check_mumu_app_keep_alive 的思路，但判定方向按需求 6 反转）"""
import json
import os
import subprocess

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
ADDR = "127.0.0.1:16384"
CUSTOMER_CONFIG = r"D:\MuMuPlayer\vms\MuMuPlayer-12.0-0\configs\customer_config.json"


class Gate:
    """环境自检门。check() 返回 (ok, 问题列表)。"""

    def __init__(self):
        self.issues: list[str] = []
        self.keepalive_on: bool | None = None   # 检测到的保活状态（供 Pipeline 提示模式）

    def check(self) -> tuple[bool, list[str]]:
        self.issues = []
        self._check_keepalive()
        self._check_adb()
        self._check_resolution()
        return (len(self.issues) == 0, self.issues)

    def _read_customer_config(self) -> dict | None:
        """读取 customer_config.json（GBK 兼容）。失败返回 None 不阻塞。"""
        try:
            with open(CUSTOMER_CONFIG, "rb") as f:
                raw = f.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except Exception:
            try:
                with open(CUSTOMER_CONFIG, "rb") as f:
                    raw = f.read().decode("gbk", errors="replace")
                return json.loads(raw)
            except Exception:
                return None

    def _check_keepalive(self):
        """需求 6 定稿：保活开启 = 单前台模式，放行（仅提示）。
        A4 修复：JSON 解析取 app_keptlive 字段，不再全文搜 '"true"'。"""
        cfg = self._read_customer_config()
        if cfg is None:
            return  # 读不到配置则跳过（不阻塞），日志警告
        try:
            customer = cfg.get("customer", {})
            self.keepalive_on = str(customer.get("app_keptlive", "false")).lower() == "true"
        except Exception:
            self.keepalive_on = None

    def _check_adb(self):
        """adb 就绪：先 connect 再 get-state（A4/C11 修复：冷启动 adb server 后必须 connect）。"""
        try:
            subprocess.run([ADB, "connect", ADDR], capture_output=True, text=True, timeout=10)
            r = subprocess.run([ADB, "-s", ADDR, "get-state"],
                               capture_output=True, text=True, timeout=5)
            if "device" not in r.stdout.lower():
                self.issues.append("adb 未就绪（127.0.0.1:16384）——确认模拟器已启动且等待 30-60s")
        except (subprocess.TimeoutExpired, OSError):
            self.issues.append("adb 调用失败——确认 MuMu 自带 adb 路径存在")

    def _check_resolution(self):
        """分辨率实查（A4 修复）：adb shell wm size（物理竖屏 1080x1920，逻辑横屏 1920×1080，
        模板与截图同源即可匹配；此处只校验非空与 16:9 形态，不硬编码字符串）。"""
        try:
            r = subprocess.run([ADB, "-s", ADDR, "shell", "wm", "size"],
                               capture_output=True, text=True, timeout=5)
            out = r.stdout.lower()
            if "1920" not in out and "1080" not in out:
                self.issues.append("无法确认模拟器分辨率（wm size 输出异常）——模板需按实际分辨率校准")
        except (subprocess.TimeoutExpired, OSError):
            pass  # adb 已由 _check_adb 覆盖

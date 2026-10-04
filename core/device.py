# -*- coding: utf-8 -*-
"""设备抽象：模拟器（adb，本期）与 PC 客户端（Windows 窗口，扩展位）。
前台校验双通道：adb dumpsys window + MuMuManager app info（官方 active 字段）。"""
import json
import logging
import subprocess
import time
from typing import Optional

import cv2
import numpy as np

from .exceptions import EmulatorDownError

log = logging.getLogger("autofw")  # P0 修复（静态审查发现）：emulator_restart 引用 log 但未定义


def _locate_mumu_tool(name):
    """定位 MuMu 12 的 adb.exe / MuMuManager.exe（开源发行用：原写死路径仅适用单机）。
    优先级：AUTOFW_MUMU_HOME 环境变量 → 注册表卸载信息（DisplayName 含 MuMu）
    → 常见安装位置扫描 → 旧版默认值 D:/MuMuPlayer/nx_main（保持原行为兜底）。"""
    import os as _os
    cand = []
    home = _os.environ.get("AUTOFW_MUMU_HOME", "")
    if home:
        cand += [_os.path.join(home, "nx_main", name), _os.path.join(home, name)]
    try:  # 注册表卸载信息反查安装目录
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    k = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                       0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with k:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(k, sub) as sk:
                                dn = (winreg.QueryValueEx(sk, "DisplayName")[0] or "")
                                if "mumu" not in dn.lower():
                                    continue
                                loc = ""
                                try:
                                    loc = winreg.QueryValueEx(sk, "InstallLocation")[0] or ""
                                except OSError:
                                    pass
                                if not loc:
                                    try:
                                        icon = winreg.QueryValueEx(sk, "DisplayIcon")[0] or ""
                                        loc = icon.split(",")[0]
                                    except OSError:
                                        pass
                                if loc:
                                    d = _os.path.dirname(_os.path.normpath(loc))
                                    cand += [_os.path.join(d, "nx_main", name),
                                             _os.path.join(d, name)]
                        except OSError:
                            continue
    except Exception:
        pass
    for base in (r"C:\Program Files\Netease", r"C:\Program Files (x86)\Netease",
                 r"D:\Program Files\Netease", "C:\\", "D:\\", "E:\\"):
        try:  # 目录名含 mumu 的常见安装位置
            if _os.path.isdir(base):
                for entry in _os.scandir(base):
                    n = entry.name.lower()
                    if entry.is_dir() and n.startswith("mumu"):
                        cand += [_os.path.join(entry.path, "nx_main", name),
                                 _os.path.join(entry.path, name)]
        except OSError:
            pass
    cand.append(_os.path.join("D:", "MuMuPlayer", "nx_main", name))  # 旧版默认值兜底
    for p in cand:
        if p and _os.path.isfile(p):
            return p
    return cand[-1]


ADB = _locate_mumu_tool("adb.exe")
MUMU_MANAGER = _locate_mumu_tool("MuMuManager.exe")
ADDR = "127.0.0.1:16384"
VM_INDEX = 0  # 实例索引（多实例时动态取，勿硬编码）


class BaseDevice:
    """设备抽象基类：PC 客户端扩展位（WGC/BitBlt 截图 + PostMessage 输入，参考 ok-ww）。"""

    def screencap(self) -> np.ndarray:
        raise NotImplementedError

    def tap(self, x: int, y: int):
        raise NotImplementedError

    def is_foreground(self, package: str) -> bool:
        raise NotImplementedError

    def start_app(self, package: str, activity: Optional[str] = None):
        raise NotImplementedError

    def close_app(self, package: str) -> bool:
        """关闭应用，返回 True=确认已关闭。"""
        raise NotImplementedError


class EmulatorDevice(BaseDevice):
    """MuMu 12 模拟器设备：adb 主通道 + MuMuManager 官方通道兜底/确认。"""

    def __init__(self, adb: str = ADB, addr: str = ADDR, manager: str = MUMU_MANAGER,
                 vm_index: int = VM_INDEX, timeout: float = 10.0):
        self.adb = adb
        self.addr = addr
        self.manager = manager
        self.vm_index = vm_index
        self.timeout = timeout

    # ---- adb 通道 ----
    def _adb_ok(self) -> bool:
        try:
            r = subprocess.run([self.adb, "-s", self.addr, "get-state"],
                               capture_output=True, timeout=6)
            return r.returncode == 0 and b"device" in r.stdout
        except Exception:
            return False

    def _probe_addr(self) -> str:
        # 探测通道（自 _ensure_addr 提取）：优先 MuMuManager 官方通道读取当前 adb_port
        # （模拟器重启后端口会变），其次解析 adb devices。更新 self.addr 并返回。
        try:
            out = self.mm_run(["info", "-v", str(self.vm_index)], timeout=8)
            for line in out.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    info = json.loads(line)
                except Exception:
                    continue
                if info.get("is_main") or str(info.get("index")) == str(self.vm_index):
                    port = info.get("adb_port")
                    if port:
                        cand = "%s:%s" % (info.get("adb_host_ip") or "127.0.0.1", port)
                        if cand != self.addr:
                            try:
                                subprocess.run([self.adb, "connect", cand],
                                               capture_output=True, timeout=8)
                            except Exception:
                                pass
                        self.addr = cand
                        return self.addr
        except Exception:
            pass
        try:
            r = subprocess.run([self.adb, "devices"], capture_output=True, timeout=6)
            for line in r.stdout.decode("utf-8", "replace").splitlines():
                parts = line.split()
                # 2026-09-09 白名单：只接受本机回环模拟器地址；USB 真机 serial
                # 一律跳过——多设备在线时防止兜底探测把目标串台到外部真机。
                if (len(parts) == 2 and parts[1] == "device"
                        and parts[0].startswith("127.0.0.1:")):
                    self.addr = parts[0]
                    return self.addr
        except Exception:
            pass
        return self.addr

    def _ensure_addr(self) -> str:
        # 地址失效自愈：_adb_ok 直接过 → _probe_addr 探测（MuMuManager info / adb devices）→
        # 仍不通则复位 adb server（kill-server + start-server）后重新探测。
        # adb server 本身挂掉时前几级会全部静默失败，kill-server 复位是最后一道自愈。
        if self._adb_ok():
            return self.addr
        # 2026-09-09 修复：模拟器 adb 端口在监听但未注册到（新起的）adb server 时，
        # 先重连原地址自愈——绝不轻易走兜底探测换设备（多设备在线时防真机串台）。
        if self.addr.startswith("127.0.0.1:"):
            try:
                subprocess.run([self.adb, "connect", self.addr],
                               capture_output=True, timeout=8)
            except Exception:
                pass
            if self._adb_ok():
                return self.addr
        addr = self._probe_addr()
        if self._adb_ok():
            return addr
        try:
            subprocess.run([self.adb, "kill-server"], capture_output=True, timeout=10)
        except Exception:
            pass
        try:
            subprocess.run([self.adb, "start-server"], capture_output=True, timeout=10)
        except Exception:
            pass
        return self._probe_addr()

    def shell(self, cmd: str, timeout: Optional[float] = None) -> str:
        """执行 adb shell 命令。Windows 下必须按 bytes 接收再 UTF-8 解码
        （text=True 用 GBK 解码会崩：'gbk' codec can't decode byte 0xaa）。"""
        full = [self.adb, "-s", self.addr, "shell", cmd]
        r = subprocess.run(full, capture_output=True, timeout=timeout or self.timeout)
        if r.returncode != 0:
            # 地址失效自愈：换地址后重试一次（模拟器重启后 adb 端口会变）
            self._ensure_addr()
            full = [self.adb, "-s", self.addr, "shell", cmd]
            r = subprocess.run(full, capture_output=True, timeout=timeout or self.timeout)
            err = r.stderr.decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"adb shell failed: {cmd} -> {err}")
        return r.stdout.decode("utf-8", errors="replace")

    def screencap(self) -> np.ndarray:
        r = subprocess.run([self.adb, "-s", self.addr, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=self.timeout)
        if not r.stdout:
            # 地址失效自愈：换地址后重试一次（模拟器重启后 adb 端口会变）
            self._ensure_addr()
            r = subprocess.run([self.adb, "-s", self.addr, "exec-out", "screencap", "-p"],
                               capture_output=True, timeout=self.timeout)
        if not r.stdout:
            # M3 实测修复（E21）：断 adb 时 screencap 返回空，抛 RuntimeError 走容灾而非 cv2 崩溃
            raise RuntimeError("screencap returned empty output (adb disconnected?)")
        img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("screencap decode failed (black/empty frame?)")
        return img

    def tap(self, x: int, y: int):
        self.shell(f"input tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    def key(self, keycode: str):
        self.shell(f"input keyevent {keycode}")

    # ---- MuMuManager 官方通道（前台校验/关闭确认）----
    def mm_run(self, args: list[str], timeout: float = 15.0) -> str:
        """执行 MuMuManager 命令，UTF-8 容错解码（同 shell 的 GBK 问题）。"""
        r = subprocess.run([self.manager, *args], capture_output=True, timeout=timeout)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"MuMuManager failed: {args} -> {err}")
        return r.stdout.decode("utf-8", errors="replace")

    def active_app(self) -> Optional[str]:
        """当前激活应用包名（官方语义=最前端 tab；保活单前台模式下即目标应用）。"""
        try:
            out = self.mm_run(["control", "-v", str(self.vm_index), "app", "info", "-i"])
            data = json.loads(out)
            return data.get("active")
        except Exception:
            return None

    def app_state(self, package: str) -> Optional[str]:
        """应用状态：running / stopped / not_installed（官方字段）。"""
        try:
            out = self.mm_run(["control", "-v", str(self.vm_index), "app", "info",
                               "-pkg", package])
            data = json.loads(out)
            return data.get("state")
        except Exception:
            return None

    def mm_close_app(self, package: str) -> bool:
        """MuMuManager 官方关闭（force-stop 杀不死的 persistent 应用兜底）。"""
        self.mm_run(["control", "-v", str(self.vm_index), "app", "close", "-pkg", package])
        return True

    # ---- 前台校验（双通道，E1）----
    def current_focus(self) -> Optional[str]:
        """通道一：dumpsys window（B4 修复：`dumpsys window windows` 在本机 Android 12 无输出，
        正确命令是 `dumpsys window`；取最后一个非 null 的 mCurrentFocus，纯字符串解析包名
        ——避免正则转义在各 shell 层的兼容问题）。"""
        try:
            out = self.shell("dumpsys window")
        except RuntimeError:
            return None
        focus = None
        for line in out.splitlines():
            if "mCurrentFocus" in line and "null" not in line and "Window{" in line:
                # '  mCurrentFocus=Window{xxx u0 com.pkg/Activity}'
                try:
                    rest = line.split("Window{", 1)[1].split("}", 1)[0]
                    parts = rest.split(" ")
                    if len(parts) >= 3:
                        focus = parts[2].split("/")[0]
                except (IndexError, ValueError):
                    continue
        return focus

    def is_foreground(self, package: str) -> bool:
        """双通道确认：dumpsys window 或 MuMuManager active 任一命中即认为在前台。
        （两通道结果不一致时，最终由 Recovery 的截图特征通道兜底——以视觉为准）"""
        cur = self.current_focus()
        if cur and cur == package:
            return True
        active = self.active_app()
        return bool(active and active == package)

    # ---- 启停 ----
    def start_app(self, package: str, activity: Optional[str] = None):
        if activity:
            self.shell(f"am start -n {package}/{activity}")
        else:
            self.mm_run(["control", "-v", str(self.vm_index), "app", "launch", "-pkg", package])

    def close_app(self, package: str) -> bool:
        """关闭：force-stop 主 → 复查（app_state）→ 杀不死则 MuMuManager close 兜底。
        force-stop 语义：杀进程+清 task（gityuan 源码实证）；persistent=true 应用免疫（E6）。"""
        self.shell(f"am force-stop {package}")
        time.sleep(1.0)
        for _ in range(2):
            state = self.app_state(package)
            if state in ("stopped", "not_installed"):
                return True
            time.sleep(1.0)
        # force-stop 失效（persistent 应用）→ 官方通道兜底
        self.mm_close_app(package)
        time.sleep(1.0)
        return self.app_state(package) in ("stopped", "not_installed")

    # ---- 连接维护 ----
    def ping(self) -> bool:
        """连接健康检查：`adb -s <addr> get-state`（get-state 是 adb 顶层命令，不是 shell 子命令）。"""
        try:
            r = subprocess.run([self.adb, "-s", self.addr, "get-state"],
                               capture_output=True, text=True, timeout=5)
            return "device" in r.stdout.lower()
        except Exception:
            return False

    def reconnect(self, attempts: int = 3):
        """重连序列（E21）；全部失败抛 EmulatorDownError（B3 修复：由 Recovery 降级链处理）。"""
        for _ in range(attempts):
            subprocess.run([self.adb, "connect", self.addr], capture_output=True, timeout=10)
            if self.ping():
                return
            subprocess.run([self.adb, "kill-server"], capture_output=True, timeout=10)
            subprocess.run([self.adb, "start-server"], capture_output=True, timeout=10)
            time.sleep(3)
        raise EmulatorDownError("adb reconnect failed")

    # ---- 三级心跳（W-05/R1-14）：模拟器卡死判定 ----
    def health_check(self) -> int:
        """返回 0=正常；1=adb 异常；2=shell 无响应；3=模拟器卡死/进程消失。
        级别 2/3 由 Recovery 决定是否重启模拟器（人工介入时机 15min 见执行文档 M3）。"""
        # 级别 1：adb devices 状态
        try:
            r = subprocess.run([self.adb, "-s", self.addr, "get-state"],
                               capture_output=True, text=True, timeout=5)
            if "device" not in r.stdout.lower():
                return 1
        except Exception:
            return 1
        # 级别 2：adb shell echo 探测（3s 超时）
        try:
            if "ok" not in self.shell("echo ok", timeout=3):
                return 2
        except Exception:
            return 2
        # 级别 3：MuMuManager 状态 + Windows 进程存在性
        try:
            out = self.mm_run(["info", "-v", str(self.vm_index)], timeout=5)
            if "\"is_android_started\": true" not in out and "\"is_process_started\": true" not in out:
                return 3
        except Exception:
            return 3
        return 0

    def emulator_restart(self, wait_adb: int = 90) -> bool:
        """降级链第 4 级（W-05）：MuMuManager shutdown + launch，等 adb 就绪。
        失败抛 EmulatorDownError。"""
        log.info("emulator_restart: shutting down instance %d", self.vm_index)
        try:
            self.mm_run(["control", "-v", str(self.vm_index), "shutdown"])
        except Exception as e:
            log.warning("shutdown failed (may already be down): %s", e)
        time.sleep(5)
        try:
            self.mm_run(["control", "-v", str(self.vm_index), "launch"])
        except Exception as e:
            log.warning("launch failed: %s", e)
        # 等 adb 就绪（MuMu 启动后 adb 30-60s 才可用，E8/MAA#9410）
        deadline = time.time() + wait_adb
        while time.time() < deadline:
            try:
                self.reconnect(attempts=2)
                return True
            except Exception:
                time.sleep(5)
        raise EmulatorDownError("emulator restart failed: adb not ready")

    def _mm_run(self, *args, **kw):
        return self.mm_run(*args, **kw)


class PcDevice(BaseDevice):
    """PC 客户端设备（扩展位，二期）：WGC/BitBlt 截图 + PostMessage 输入。
    实现参考：D:/ok-ww/data/apps/ok-ww/repo/ok/device/capture_methods/windows_graphics.py
    （Windows.Graphics.Capture）与 interaction_methods/post_message.py（win32gui.PostMessage）。"""
    # TODO(M2+): 按 ok-ww 的 windows_graphics.py / post_message.py 实现
    pass

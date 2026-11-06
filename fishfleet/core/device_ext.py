"""设备扩展能力 —— 对齐汇城的 30+ 个后端命令。

本模块只做「adb / 系统能力」的封装，不掺业务逻辑。
所有函数都设计成「失败返回 None 或空列表，不抛异常」，方便上层批量调用。

对照表（汇城命令 → 本文件函数）
--------------------------------------------------
P0 右键菜单
  uninstall_apk           uninstall_apk
  get_apk_info            get_apk_info
  get_density             get_density
  get_device_info         get_device_info
  get_device_mode         get_device_mode
  switch_device_mode      switch_device_mode
  reconnect_device        reconnect_device
  restart_adb             restart_adb

P1 无障碍 / HID
  install_accessible        accessible_install_app
  add_accessible            accessible_add
  start_accessible          accessible_start
  switch_accessible_mode    accessible_enable / accessible_disable
  get_accessible_devices    accessible_list
  device_is_accessible      accessible_is_granted
  close_accessible_device_serial  accessible_close_by_ip
  is_hid_mode             hid_is_mode
  switch_hid_mode         hid_switch_mode
  install_hid_app         hid_install_app
  check_hid_app_installed hid_check_installed
  check_adb_device        adb_check_device
  switch_adb_mode         adb_switch_mode
  remove_hid_driver       hid_remove_driver
  get_usb_devices         usb_list
  has_hid_devices         usb_has_hid

P2 网络 / IP
  get_serial_ip           device_ip
  get_ip_serial           ip_to_serial
  get_arp_out             arp_table
  push_scan_ips           scan_subnet
  get_external_ip         external_ip
  get_ws_port             ws_port

P3 其他
  wallpapers_device       set_wallpaper
  close_device            close_device
  device_disconnect       disconnect_device
  download_image          pull_screenshot
  get_file_md5            file_md5
  action_act              run_action_script
  exec_autojs             autojs_run
  stop_autojs             autojs_stop
  read_sys_config         read_sys_config
  get_system_config       system_config
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# adb 定位
# ---------------------------------------------------------------------------

_DEFAULT_ADB = __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()


def adb_path() -> str:
    """拿 adb 可执行路径（优先配置，其次汇城自带的，最后 PATH）。"""
    try:
        from .config import get_config

        p = get_config().devices.adb_path
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    if os.path.exists(_DEFAULT_ADB):
        return _DEFAULT_ADB
    return "adb"


def adb(serial: str, *args: str, timeout: float = 20.0) -> tuple[int, str]:
    """跑一条 adb 命令，返回 (returncode, output)。

    ★ 统一入口，所有设备操作都走这里，避免到处写 subprocess。
    """
    cmd = [adb_path()]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return -1, f"ERROR: {e}"


def shell(serial: str, cmd: str, timeout: float = 20.0) -> str:
    """跑 adb shell，返回输出文本。"""
    rc, out = adb(serial, "shell", cmd, timeout=timeout)
    return out


# ---------------------------------------------------------------------------
# P0：应用管理
# ---------------------------------------------------------------------------


def uninstall_apk(serial: str, package: str, keep_data: bool = False) -> dict:
    """卸载应用（对齐汇城 uninstall_apk）。"""
    args = ["uninstall"]
    if keep_data:
        args.append("-k")
    args.append(package)
    rc, out = adb(serial, *args, timeout=60)
    ok = "Success" in out
    return {
        "ok": ok,
        "package": package,
        "keep_data": keep_data,
        "message": out[:300],
    }


def get_apk_info(serial: str, package: str) -> dict:
    """取应用详情（对齐汇城 get_apk_info）。

    用 dumpsys package 解析：版本名、版本号、首装时间、更新时间、
    数据目录、APK 路径、targetSdk、是否系统应用。
    """
    out = shell(serial, f"dumpsys package {package}", timeout=25)
    if not out or "Unable to find package" in out:
        return {"ok": False, "package": package, "error": "未安装"}

    info: dict[str, Any] = {"ok": True, "package": package}

    patterns = {
        "version_name": r"versionName=([^\s]+)",
        "version_code": r"versionCode=(\d+)",
        "min_sdk": r"minSdk=(\d+)",
        "target_sdk": r"targetSdk=(\d+)",
        "first_install": r"firstInstallTime=([^\n]+)",
        "last_update": r"lastUpdateTime=([^\n]+)",
        "data_dir": r"dataDir=([^\s]+)",
        "code_path": r"codePath=([^\s]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, out)
        if m:
            info[key] = m.group(1).strip()

    # 这里只解析启动组件，不是应用显示名称。
    label = shell(
        serial,
        f"cmd package resolve-activity --brief {package} 2>/dev/null | tail -1",
        timeout=10,
    )
    info["launcher"] = label.strip() if label else ""

    # 系统应用判断
    info["system"] = "/system/" in (info.get("code_path") or "") or "/system/" in (
        info.get("data_dir") or ""
    )

    # APK 大小
    apk_path = info.get("code_path")
    if apk_path:
        size = shell(serial, f"stat -c %s {apk_path} 2>/dev/null", timeout=8).strip()
        if size.isdigit():
            info["size_bytes"] = int(size)
            info["size_mb"] = round(int(size) / 1024 / 1024, 2)

    return info


def list_packages(serial: str, third_party_only: bool = False) -> list[str]:
    """列出包名（对齐汇城 get_apk_list 的底层）。"""
    flag = "-3" if third_party_only else ""
    out = shell(serial, f"pm list packages {flag}".strip(), timeout=25)
    return [
        ln[len("package:"):].strip()
        for ln in out.splitlines()
        if ln.startswith("package:")
    ]


# ---------------------------------------------------------------------------
# P0：屏幕与设备信息
# ---------------------------------------------------------------------------


def get_density(serial: str) -> dict:
    """取屏幕密度（对齐汇城 get_density）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", "wm density", timeout=10)
    info: dict[str, Any] = {"ok": False, "raw": out}
    if rc != 0:
        info['error'] = '读取屏幕密度失败'
        return info
    m = re.search(r"Override density:\s*(\d+)", out) or re.search(r"Physical density:\s*(\d+)", out)
    if m and int(m.group(1)) > 0:
        info.update(density=int(m.group(1)), ok=True, source='wm')
        return info
    rc, value = adb(serial, "shell", "getprop ro.sf.lcd_density", timeout=8)
    value = value.strip()
    if rc == 0 and value.isdigit() and int(value) > 0:
        info.update(density=int(value), ok=True, source='property_fallback')
    return info


def get_size(serial: str) -> dict:
    """取屏幕分辨率（wm size，注意这是「应用区」，不是截图尺寸）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", "wm size", timeout=10)
    info: dict[str, Any] = {"ok": False, "raw": out}
    if rc != 0:
        info['error'] = '读取屏幕尺寸失败'
        return info
    m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
        info["width"] = int(m.group(1))
        info["height"] = int(m.group(2))
        info["ok"] = True
    return info


def get_device_info(serial: str) -> dict:
    """设备完整信息（对齐汇城 get_device_info）。

    一条 adb shell 拿多个 prop，避免多次往返。
    """
    props = [
        "ro.product.model",
        "ro.product.brand",
        "ro.product.manufacturer",
        "ro.product.device",
        "ro.build.version.release",
        "ro.build.version.sdk",
        "ro.build.display.id",
        "ro.serialno",
        "ro.product.cpu.abi",
        "ro.build.characteristics",
        "persist.sys.timezone",
        "ro.sf.lcd_density",
    ]
    cmd = "; ".join(f"getprop {p}" for p in props)
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", cmd, timeout=20)
    if rc != 0:
        return {"ok": False, "serial": serial, "error": "读取设备属性失败"}
    lines = [ln.strip() for ln in out.splitlines()]

    keys = [
        "model", "brand", "manufacturer", "device", "android_version",
        "sdk", "build_id", "device_serial", "cpu_abi", "characteristics",
        "timezone", "density",
    ]
    info: dict[str, Any] = {"ok": True, "serial": serial}
    for k, v in zip(keys, lines):
        info[k] = v

    # 电池
    bat = shell(serial, "dumpsys battery | grep -E 'level|status|temperature'", timeout=10)
    for ln in bat.splitlines():
        m = re.match(r"\s*(level|status|temperature):\s*(-?\d+)", ln)
        if m:
            info[f"battery_{m.group(1)}"] = int(m.group(2))

    # 屏幕状态
    power = shell(serial, "dumpsys power | grep -E 'mWakefulness='", timeout=10)
    m = re.search(r"mWakefulness=(\w+)", power)
    if m:
        info["screen_state"] = m.group(1)

    # USB 模式
    info["usb_mode"] = shell(serial, "getprop sys.usb.state", timeout=8).strip()

    return info


def get_device_mode(serial: str) -> dict:
    """当前 USB 模式（对齐汇城 get_device_mode）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc_state, state = adb(serial, "shell", "getprop sys.usb.state", timeout=8)
    rc_config, config = adb(serial, "shell", "getprop sys.usb.config", timeout=8)
    if rc_state != 0 or rc_config != 0:
        return {"ok": False, "error": "读取 USB 模式失败"}
    state, config = state.strip(), config.strip()
    # 常见值：mtp / ptp / rndis / adb / mtp,adb / charging
    modes = []
    for m in (state + "," + config).split(","):
        m = m.strip().lower()
        if m and m not in modes:
            modes.append(m)
    return {
        "ok": True,
        "state": state,
        "config": config,
        "modes": modes,
        "has_adb": "adb" in modes,
        "has_mtp": "mtp" in modes,
        "has_ptp": "ptp" in modes,
        "has_rndis": "rndis" in modes,
    }


# USB 模式预设（对齐汇城 switch_device_mode 的选项）
USB_MODES = {
    "charging": "none",
    "mtp": "mtp",
    "ptp": "ptp",
    "rndis": "rndis",
    "midi": "midi",
    "adb": "adb",
    "mtp_adb": "mtp,adb",
    "ptp_adb": "ptp,adb",
    "rndis_adb": "rndis,adb",
    "midi_adb": "midi,adb",
}


def switch_device_mode(serial: str, mode: str) -> dict:
    """切换 USB 模式（对齐汇城 switch_device_mode / switch_all_device_mode）。

    mode 可以是 USB_MODES 的 key，也可以是原始值（如 "mtp,adb"）。
    注意：切换后 adb 可能短暂断开，建议等 2-3 秒再操作。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    value = USB_MODES.get(mode)
    if value is None:
        return {"ok": False, "error": "不支持的 USB 模式"}
    rc, out = adb(serial, "shell", f"svc usb setFunctions {value}", timeout=15)
    if rc != 0 or re.search(r'(?i)\b(error|exception|failed|denied)\b', out):
        return {"ok": False, "error": out[:200] or "USB 切换命令失败"}
    time.sleep(1.5)
    after = get_device_mode(serial)
    expected = set(value.split(','))
    observed = set(after.get('state', '').split(',')) if after.get('ok') else set()
    confirmed = bool(after.get('ok') and expected == observed)
    return {"ok": True, "requested": mode, "applied": value,
            "confirmed": confirmed, "current": after,
            "message": "设备状态已确认" if confirmed else "命令已提交，实际状态未确认"}


# ---------------------------------------------------------------------------
# P0：连接管理
# ---------------------------------------------------------------------------


def reconnect_device(serial: str, port: int = 5555) -> dict:
    """断线重连（对齐汇城 reconnect_device）。

    先尝试 adb reconnect（USB）；失败则用 TCP（无线）。
    """
    rc1, out1 = adb("", "reconnect", "device", timeout=20)
    if "success" in out1.lower() or "reconnected" in out1.lower():
        return {"ok": True, "via": "usb", "message": out1[:200]}

    # 拿设备 IP 试无线
    ip = device_ip(serial).get("ip")
    if ip:
        adb("", "disconnect", f"{ip}:{port}", timeout=10)
        time.sleep(1)
        rc2, out2 = adb("", "connect", f"{ip}:{port}", timeout=20)
        ok = "connected" in out2.lower() and "cannot" not in out2.lower()
        return {"ok": ok, "via": "wifi", "addr": f"{ip}:{port}", "message": out2[:200]}

    return {"ok": False, "message": out1[:200] or "重连失败"}


def restart_adb() -> dict:
    """重启 adb 服务（对齐汇城 restart_adb）。"""
    a = adb_path()
    try:
        r1 = subprocess.run([a, "kill-server"], capture_output=True, text=True, timeout=15,
                            encoding="utf-8", errors="ignore")
        time.sleep(1.0)
        r2 = subprocess.run([a, "start-server"], capture_output=True, text=True, timeout=25,
                            encoding="utf-8", errors="ignore")
        time.sleep(1.5)
        r3 = subprocess.run([a, "devices"], capture_output=True, text=True, timeout=15,
                            encoding="utf-8", errors="ignore")
        n = len([x for x in (r3.stdout or "").splitlines() if re.search(r"\s+device$", x)])
        return {
            "ok": True,
            "devices": n,
            "kill": (r1.stdout or "")[:100],
            "start": (r2.stdout or "")[:100],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def close_device(serial: str) -> dict:
    """关闭/断开设备（对齐汇城 close_device）。"""
    rc, out = adb("", "disconnect", serial, timeout=10)
    return {"ok": True, "serial": serial, "message": out[:200]}


def disconnect_device(serial: str) -> dict:
    """断开设备（对齐汇城 device_disconnect）。"""
    return close_device(serial)


# ---------------------------------------------------------------------------
# P1：无障碍模式
# ---------------------------------------------------------------------------
# 汇城无障碍链路（与汇城一模一样）：
# 点击开启后自动安装投屏助手 assistant.apk，授权 PerformService，
# 健康检查 MainActivity.check/autojs.check/param，广播 scrcpy 起流，
# 关闭走 close_accessible_device_serial + switch_adb_mode 回到 ADB。
ASSISTANT_PACKAGE = "com.xiaowei.assistant"
ASSISTANT_MAIN_ACTIVITY = "com.xiaowei.assistant/.MainActivity"
ASSISTANT_APK_CANDIDATES = (
    str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("assistant.apk") or ""),
    os.path.join(os.path.dirname(__file__), "..", "..", "apk", "assistant.apk"),
)
ACCESSIBLE_SERVICE = "com.xiaowei.assistant/com.xiaowei.assistant.scrcpy.PerformService"
AUTOJS_SERVICE = "com.xiaowei.assistant/com.xiaowei.assistant.autojs.AutojsService"
ACCESSIBLE_HEALTH_RESULTS = {
    "check": "11",
    "autojs_check": ("12", "14"),
    "param": "13",
}
# 汇城无障碍 14 阶段失败提示（p1-p14，与汇城一模一样）。
ACCESSIBLE_FAILURE_TEXT = {
    "p1": "已超出免费版的无障碍模式设备数量，开通会员版后无数量限制。",
    "p2": "正在为您安装【投屏助手】APP，请稍后。",
    "p3": "app录屏未授权",
    "p4": "【投屏助手】未获得无障碍权限，请您先授权。",
    "p5": "ADB未找到当前设备",
    "p6": "无障碍投屏安卓版本必须大于6.0",
    "p7": "获取手机sdk版本错误",
    "p8": "当前设备未安装'投屏助手'",
    "p9": "请检查当前设备的网络",
    "p10": "获取设备ip地址失败",
    "p11": "【投屏助手】正在启动中,请稍后再试",
    "p12": "【投屏助手】断开",
    "p13": "设备转向连接本机，请稍后再试",
    "p14": "【投屏助手】未获得悬浮窗权限，请您先授权。",
}
ACCESSIBLE_DIALOG = {
    "title": "无障碍温馨提示",
    "items": [
        "1. 将为您的手机安装并运行【投屏助手】APP。",
        "2. 请您在【投屏助手】APP启动时授权录制屏幕权限。",
        "3. 请您在手机系统设置中开启【投屏助手】APP无障碍权限。",
    ],
    "confirm": "确认转为无障碍",
}
# 电脑端无障碍通道内存状态：mode 3 与汇城一致。
_ACCESSIBLE_RUNTIME: dict[str, dict] = {}
# 正在运行的无障碍投屏会话（电脑监听、手机回连）。
_ACCESSIBLE_SESSIONS: dict[str, object] = {}


def _accessible_component_identity(component: str) -> tuple[str, str]:
    package, cls = component.split("/", 1)
    return package, package + cls if cls.startswith(".") else cls


def _read_accessible_services(serial: str) -> tuple[Optional[list[str]], str]:
    if not serial or not serial.strip():
        return None, "必须指定设备序列号"
    rc, out = adb(serial, "shell", "settings get secure enabled_accessibility_services", timeout=10)
    if rc != 0:
        return None, "读取无障碍服务失败"
    value = out.strip()
    if value in ("null", ""):
        return [], ""
    component = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+/[A-Za-z_.$][A-Za-z0-9_.$]*"
    if any(not re.fullmatch(component, s) for s in value.split(":")):
        return None, "无障碍服务列表格式无法确认"
    return value.split(":"), ""


def accessible_is_granted(serial: str) -> dict:
    """检测目标服务是否在已启用列表中，读取不确定时拒绝猜测。"""
    services, error = _read_accessible_services(serial)
    if services is None:
        return {"ok": False, "error": error}
    target = _accessible_component_identity(ACCESSIBLE_SERVICE)
    return {"ok": True, "granted": any(_accessible_component_identity(s) == target for s in services),
            "services": ":".join(services)}


def accessible_list(serial: str) -> dict:
    """列出所有无障碍服务（对齐汇城 get_accessible_devices）。"""
    out = shell(serial, "settings get secure enabled_accessibility_services", timeout=10)
    allsvc = shell(serial, "settings get secure accessibility_enabled", timeout=8)
    return {
        "ok": True,
        "enabled": allsvc.strip() == "1",
        "services": [s.strip() for s in out.split(":") if s.strip()],
    }


def accessible_enable(serial: str, service: str = ACCESSIBLE_SERVICE) -> dict:
    """开启无障碍（对齐汇城 switch_accessible_mode）。

    ★ 用 settings put secure 直接写，不需要用户手动点授权
      （前提是设备已 root 或 debuggable；普通设备仍需用户在设置里允许）
    """
    if service != ACCESSIBLE_SERVICE:
        return {"ok": False, "error": "不支持的无障碍服务"}
    services, error = _read_accessible_services(serial)
    if services is None:
        return {"ok": False, "error": error}
    target = _accessible_component_identity(service)
    if not any(_accessible_component_identity(s) == target for s in services):
        value = ':'.join(services + [service])
        rc, out = adb(serial, "shell", f"settings put secure enabled_accessibility_services '{value}'", timeout=10)
        if rc != 0 or out.strip():
            return {"ok": False, "error": "写入无障碍服务失败或结果不明确"}
    rc, out = adb(serial, "shell", "settings put secure accessibility_enabled 1", timeout=8)
    if rc != 0 or out.strip():
        return {"ok": False, "error": "启用无障碍失败或结果不明确；服务列表可能已改变"}
    status = accessible_is_granted(serial)
    if not status.get("ok") or not status.get("granted"):
        return {"ok": False, "service": service, "error": status.get("error", "回读未发现目标服务")}
    return {"ok": True, "service": service, "granted": True}


def accessible_disable(serial: str, service: str = ACCESSIBLE_SERVICE) -> dict:
    """仅移除本应用的服务；settings 读改写不是原子操作，无法避免并发覆盖。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    services, error = _read_accessible_services(serial)
    if services is None:
        return {"ok": False, "error": error}
    target = _accessible_component_identity(service)
    remaining = [s for s in services if _accessible_component_identity(s) != target]
    if remaining != services:
        value = ":".join(remaining)
        rc, out = adb(serial, "shell", f"settings put secure enabled_accessibility_services '{value}'", timeout=10)
        if rc != 0 or out.strip():
            return {"ok": False, "error": "写入无障碍服务失败或结果不明确"}

    verified, error = _read_accessible_services(serial)
    if verified is None:
        return {"ok": False, "error": "验证失败：" + error}
    if verified != remaining:
        return {"ok": False, "error": "验证失败：服务列表与预期不一致"}
    # 不改 accessibility_enabled：其他服务或并发操作可能仍依赖它。
    _ACCESSIBLE_RUNTIME.pop(serial, None)
    return {"ok": True, "granted": False}


def _accessible_apk_candidates(explicit: str = "") -> list[str]:
    """投屏助手 APK 候选路径（与汇城一模一样：优先 tools/assistant.apk）。"""
    ordered: list[str] = []
    if explicit:
        ordered.append(explicit)
    ordered.append(str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("assistant.apk") or ""))
    try:
        ordered.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "apk", "assistant.apk")))
    except Exception:
        pass
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def accessible_find_apk(apk_path: str = "") -> tuple[str, list[str]]:
    """找到本机投屏助手 APK；找不到时返回搜索过的路径。"""
    searched = _accessible_apk_candidates(apk_path)
    for candidate in searched:
        try:
            if candidate and os.path.exists(candidate):
                return candidate, searched
        except Exception:
            continue
    return "", searched


def accessible_check_assistant_installed(serial: str) -> dict:
    """检查投屏助手是否已安装（对齐汇城 p8）。"""
    if not serial or not serial.strip():
        return {"ok": False, "installed": None, "package": ASSISTANT_PACKAGE, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", f"pm list packages {ASSISTANT_PACKAGE}", timeout=15)
    if rc != 0:
        return {"ok": False, "installed": None, "package": ASSISTANT_PACKAGE, "error": out or "检查投屏助手失败"}
    installed = f"package:{ASSISTANT_PACKAGE}" in (line.strip() for line in out.splitlines())
    return {"ok": True, "installed": installed, "package": ASSISTANT_PACKAGE}


def accessible_install_app(serial: str, apk_path: str = "") -> dict:
    """安装投屏助手（对齐汇城 install_accessible / p2）。

    点击开启无障碍模式时自动调用：未安装就安装，已安装则直接复用。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    found, searched = accessible_find_apk(apk_path)
    if not found:
        return {"ok": False, "error": "找不到投屏助手 APK", "searched": searched}
    rc, out = adb(serial, "install", "-r", "-g", found, timeout=180)
    text = out or ""
    if rc == 0 and "Success" in text:
        return {"ok": True, "apk": found, "installed": True, "message": text[:300]}
    return {"ok": False, "apk": found, "installed": False, "error": text[:300] or "安装投屏助手失败"}


def accessible_sdk_ok(serial: str) -> dict:
    """Android 版本门槛（对齐汇城 p6/p7：必须大于 6.0，即 SDK > 23）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", "getprop ro.build.version.sdk", timeout=10)
    value = (out or "").strip()
    if rc != 0 or not value:
        return {"ok": False, "error": ACCESSIBLE_FAILURE_TEXT["p7"], "sdk": value}
    try:
        sdk = int(value)
    except ValueError:
        return {"ok": False, "error": ACCESSIBLE_FAILURE_TEXT["p7"], "sdk": value}
    if sdk <= 23:
        return {"ok": False, "error": ACCESSIBLE_FAILURE_TEXT["p6"], "sdk": sdk}
    return {"ok": True, "sdk": sdk}


def accessible_overlay_ok(serial: str) -> dict:
    """悬浮窗权限尽力检查（对齐汇城 p14）。

    只在明确拒绝时失败；ROM 差异导致无法判断时放行，避免误杀。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", f"appops get {ASSISTANT_PACKAGE} SYSTEM_ALERT_WINDOW", timeout=10)
    text = (out or "").strip().lower()
    if rc != 0 or not text:
        return {"ok": True, "checked": False}
    if "deny" in text or "ignore" in text:
        return {"ok": False, "error": ACCESSIBLE_FAILURE_TEXT["p14"], "detail": text[:200]}
    return {"ok": True, "checked": True, "detail": text[:200]}


def accessible_broadcast(serial: str, action: str, timeout: float = 20.0) -> dict:
    """发汇城式广播并回读输出（不解析系统级 result，只保留原文供上层判断）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    if not action:
        return {"ok": False, "error": "必须指定广播 action"}
    rc, out = adb(serial, "shell", f"am broadcast -a {action} -e serial {serial} --receiver-foreground", timeout=timeout)
    text = out or ""
    if rc != 0:
        return {"ok": False, "error": text[:300] or "广播发送失败", "output": text[:500]}
    return {"ok": True, "output": text[:2000]}


def accessible_healthcheck(serial: str) -> dict:
    """汇城三段健康检查：check=11，autojs.check=12/14，param=13。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    checks = [
        ("check", f"{ASSISTANT_PACKAGE}.MainActivity.check"),
        ("autojs_check", f"{ASSISTANT_PACKAGE}.MainActivity.autojs.check"),
        ("param", f"{ASSISTANT_PACKAGE}.MainActivity.param"),
    ]
    details: dict[str, dict] = {}
    for name, action in checks:
        result = accessible_broadcast(serial, action, timeout=20.0)
        output = result.get("output", "") if result.get("ok") else result.get("error", "")
        hit = ""
        match = re.search(r"result\s*=\s*(\d+)", output or "")
        if match:
            hit = match.group(1)
        details[name] = {"ok": result.get("ok", False), "result": hit, "output": (output or "")[:500]}
    expected = ACCESSIBLE_HEALTH_RESULTS
    ok = (
        details["check"].get("result") == expected["check"]
        and details["autojs_check"].get("result") in expected["autojs_check"]
        and details["param"].get("result") == expected["param"]
    )
    return {"ok": ok, "details": details}


def accessible_pc_ip() -> str:
    """电脑端回连 IP（与汇城“本机的 IP”一致：优先 UDP 探测，其次主机名解析）。"""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            candidate = probe.getsockname()[0]
        finally:
            probe.close()
        if candidate and not candidate.startswith("127."):
            return candidate
    except Exception:
        pass
    try:
        candidate = socket.gethostbyname(socket.gethostname())
        if candidate and not candidate.startswith("127."):
            return candidate
    except Exception:
        pass
    return "127.0.0.1"


def accessible_allocate_port() -> dict:
    """分配电脑端监听端口（与汇城“分配的端口号”一致）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", 0))
        port = int(sock.getsockname()[1])
        return {"ok": True, "port": port}
    except OSError as exc:
        return {"ok": False, "error": f"端口被占用，无障碍投屏将无法启用：{exc}"}
    finally:
        try:
            sock.close()
        except Exception:
            pass


def accessible_add(serial: str, ip: str = "", o_addr: str = "") -> dict:
    """加入待投屏队列（对齐汇城 add_accessible：按串号去重）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    entry = _ACCESSIBLE_RUNTIME.get(serial, {})
    entry.update({"serial": serial, "ip": ip, "o_addr": o_addr, "queued": True, "updated_at": time.time()})
    _ACCESSIBLE_RUNTIME[serial] = entry
    return {"ok": True, "serial": serial, "queued": True, "address": ip or o_addr or serial}


def accessible_runtime(serial: str) -> dict:
    """读取电脑端无障碍通道内存状态（mode 3 与汇城一致）。"""
    return dict(_ACCESSIBLE_RUNTIME.get(serial, {"serial": serial, "mode": 0, "is_accessible": False}))


def accessible_list_devices() -> dict:
    """无障碍在线集合（对齐汇城 ACCESSIBLE_DEVICES）。"""
    devices = [dict(entry) for entry in _ACCESSIBLE_RUNTIME.values() if entry.get("is_accessible")]
    return {"ok": True, "devices": devices, "count": len(devices)}


def accessible_start(serial: str, src: int = 0, ip: str = "", o_addr: str = "", use_soft_encoder: bool = True) -> dict:
    """完整开启链路（对齐汇城 start_accessible / switch_accessible_mode）。

    顺序：ADB 在线 → SDK 门槛 → 自动安装投屏助手 → 授权 PerformService →
    启动 MainActivity → 健康广播 → 分配端口并广播 scrcpy。
    视频 TCP 握手仍是下一步要接的真实投屏接收，目前先把可验证的控制面做实。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号", "reason": ACCESSIBLE_FAILURE_TEXT["p5"]}
    # 对齐汇城「在线设备中包括当前无障碍设备跳过：」：已在本通道里的设备不重复启动。
    existing = _ACCESSIBLE_RUNTIME.get(serial)
    if existing and existing.get("is_accessible") and src != 2:
        return {"ok": True, "serial": serial, "mode": 3, "is_accessible": True,
                "port": existing.get("port", 0), "skipped": True,
                "note": "在线设备中包括当前无障碍设备跳过"}
    state = adb(serial, "get-state", timeout=10)
    if state[0] != 0 or state[1].strip() != "device":
        return {"ok": False, "error": ACCESSIBLE_FAILURE_TEXT["p5"], "reason": ACCESSIBLE_FAILURE_TEXT["p5"]}
    sdk = accessible_sdk_ok(serial)
    if not sdk.get("ok"):
        return {"ok": False, **sdk, "reason": sdk.get("error")}
    installed = accessible_check_assistant_installed(serial)
    if not installed.get("ok"):
        return {"ok": False, **installed, "reason": installed.get("error")}
    if not installed.get("installed"):
        installing = accessible_install_app(serial)
        if not installing.get("ok"):
            return {"ok": False, **installing, "reason": ACCESSIBLE_FAILURE_TEXT["p2"]}
    granted = accessible_enable(serial, ACCESSIBLE_SERVICE)
    if not granted.get("ok"):
        reason = ACCESSIBLE_FAILURE_TEXT["p4"]
        payload = dict(granted)
        payload.update({"ok": False, "reason": reason})
        return payload
    overlay = accessible_overlay_ok(serial)
    if not overlay.get("ok"):
        return {"ok": False, **overlay, "reason": overlay.get("error")}
    rc, out = adb(serial, "shell", f"am start -n {ASSISTANT_MAIN_ACTIVITY} -e serial {serial}", timeout=20)
    if rc != 0:
        return {"ok": False, "error": (out or "")[:300] or "启动投屏助手失败", "reason": ACCESSIBLE_FAILURE_TEXT["p11"]}
    health = accessible_healthcheck(serial)
    # 汇城的 check/autojs.check/param 结果会随 App 状态变化（实测见过 11/12/13/1），
    # 不能三个全中才放行；只要 App 进程在，就以真正的 TCP 会话为准。
    running = (shell(serial, f"pidof {ASSISTANT_PACKAGE}", timeout=10) or "").strip()
    if not running and not health.get("ok"):
        return {"ok": False, "reason": ACCESSIBLE_FAILURE_TEXT["p11"], "health": health.get("details")}
    pc_ip = ip.strip() or accessible_pc_ip()
    if not pc_ip:
        return {"ok": False, "error": ACCESSIBLE_FAILURE_TEXT["p10"], "reason": ACCESSIBLE_FAILURE_TEXT["p10"]}

    # 这里只做「装好、授权、拉起 App」的准备；真正的 TCP 会话由投屏管线
    # (StreamWorker) 负责：它会在取流时调用 AccessibleScrcpySession 广播并收流，
    # 这样视频帧有人消费、控制流也有人用，不会出现“连上没人读”的假连接。
    entry = {
        "serial": serial,
        "mode": 3,
        "is_accessible": True,
        "port": 0,
        "ip": pc_ip,
        "o_addr": o_addr,
        "src": src,
        "video_handshake": "ready",
        "updated_at": time.time(),
    }
    _ACCESSIBLE_RUNTIME[serial] = entry
    return {"ok": True, "serial": serial, "mode": 3, "is_accessible": True,
            "port": 0, "ip": pc_ip,
            "note": "已进入无障碍模式，画面与控制由投屏管线建立"}


def accessible_failure_reason(detail: str) -> str:
    """把失败详情归一到汇城的 p1–p14 文案。"""
    text = (detail or "").strip()
    for value in ACCESSIBLE_FAILURE_TEXT.values():
        if text == value:
            return value
    return text or ACCESSIBLE_FAILURE_TEXT["p12"]


# ---------------------------------------------------------------------------
# P1：无线调试（Android 11+ 无线调试 / adb pair + adb connect）
# ---------------------------------------------------------------------------

WIRELESS_PORT_RANGE = (30000, 50000)


def wireless_parse_target(target: str) -> tuple[str, int]:
    """解析 "IP:端口" 或 "IP"，端口缺省为 0（表示需要扫描）。"""
    text = (target or "").strip()
    if not text:
        return "", 0
    if text.count(":") == 1:
        host, _, port_text = text.partition(":")
        if port_text.isdigit():
            port = int(port_text)
            if 1 <= port <= 65535:
                return host.strip(), port
    return text, 0


def _probe_port(host: str, port: int, timeout: float = 0.6) -> Optional[int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return port if sock.connect_ex((host, port)) == 0 else None
    except OSError:
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def wireless_scan_ports(host: str, lo: int = 0, hi: int = 0) -> dict:
    """扫描某台手机的无线调试连接端口（默认 30000–50000）。"""
    host = (host or "").strip()
    if not host:
        return {"ok": False, "error": "必须指定手机 IP", "ports": []}
    low = int(lo) if lo else WIRELESS_PORT_RANGE[0]
    high = int(hi) if hi else WIRELESS_PORT_RANGE[1]
    if not (1 <= low <= 65535 and 1 <= high <= 65535 and low <= high):
        return {"ok": False, "error": "端口范围不合法", "ports": []}
    if high - low > 30000:
        return {"ok": False, "error": "端口范围过大（上限 30000 个）", "ports": []}
    ports: list[int] = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=400) as pool:
            for result in pool.map(lambda p: _probe_port(host, p), range(low, high + 1)):
                if result:
                    ports.append(result)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"扫描失败: {type(exc).__name__}", "ports": []}
    return {"ok": True, "ip": host, "ports": sorted(ports), "range": [low, high]}


def wireless_pair(host: str, port: int, code: str) -> dict:
    """用配对码配对（对应手机「使用配对码配对设备」页面）。"""
    host = (host or "").strip()
    code = (code or "").strip()
    if not host or not port or not code:
        return {"ok": False, "error": "必须提供 IP、配对端口和配对码"}
    rc, out = adb("", "pair", f"{host}:{int(port)}", code, timeout=40)
    text = out or ""
    ok = rc == 0 and "Successfully paired" in text
    return {"ok": ok, "ip": host, "port": int(port), "message": text[:300],
            **({} if ok else {"error": text[:300] or "配对失败"})}


def wireless_connect(host: str, port: int) -> dict:
    """连接无线调试端口。"""
    host = (host or "").strip()
    if not host or not port:
        return {"ok": False, "error": "必须提供 IP 和连接端口"}
    rc, out = adb("", "connect", f"{host}:{int(port)}", timeout=30)
    text = out or ""
    ok = rc == 0 and ("connected to" in text.lower())
    return {"ok": ok, "ip": host, "port": int(port), "serial": f"{host}:{int(port)}",
            "message": text[:300], **({} if ok else {"error": text[:300] or "连接失败"})}


def wireless_add(target: str, code: str = "", lo: int = 0, hi: int = 0) -> dict:
    """一步添加无线设备：配对（可选）→ 扫端口 → 连接。"""
    host, port = wireless_parse_target(target)
    if not host:
        return {"ok": False, "error": "请输入手机 IP 或 IP:端口"}
    steps: list[dict] = []
    pairing_port = port if (code and port) else 0

    if code:
        if not pairing_port:
            return {"ok": False, "error": "配对需要填写手机上的配对端口（IP:端口）"}
        paired = wireless_pair(host, pairing_port, code)
        steps.append({"step": "pair", **paired})
        if not paired.get("ok"):
            return {"ok": False, "error": paired.get("error", "配对失败"), "steps": steps}

    connect_port = port if (port and not code) else 0
    if not connect_port:
        scanned = wireless_scan_ports(host, lo, hi)
        steps.append({"step": "scan", "ok": scanned.get("ok"), "ports": scanned.get("ports", [])})
        candidates = [p for p in scanned.get("ports", []) if p != pairing_port]
        if not candidates:
            return {"ok": False, "error": "未扫描到无线调试连接端口，请确认手机「无线调试」已开启且与电脑同网段",
                    "steps": steps}
        connect_port = candidates[0]

    connected = wireless_connect(host, connect_port)
    steps.append({"step": "connect", **connected})
    if not connected.get("ok"):
        return {"ok": False, "error": connected.get("error", "连接失败"), "steps": steps}
    return {"ok": True, "serial": connected["serial"], "ip": host, "port": connect_port, "steps": steps}


def _local_prefix() -> str:
    """本机网段前缀，例如 192.168.31。"""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        ip = ""
    parts = ip.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else "192.168.1"


def _ping_alive(ip: str, timeout_ms: int = 300) -> bool:
    import subprocess as _sp
    try:
        r = _sp.run(["ping", "-n", "1", "-w", str(timeout_ms), ip],
                    capture_output=True, text=True, errors="ignore", timeout=3)
        return "TTL=" in (r.stdout or "").upper()
    except Exception:
        return False


def wifi_scan(prefix: str = "", deep: bool = False, lo: int = 0, hi: int = 0) -> dict:
    """网段主动扫描：存活主机 → 5555 → （可选）无线调试高段端口 → 自动连接。

    只发现「开着 ADB/无线调试」的手机；开过无障碍但关掉调试的设备没有可探测特征。
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    net = (prefix or "").strip() or _local_prefix()
    if net.count(".") != 2:
        return {"ok": False, "error": "网段需形如 192.168.31"}
    started = _time.time()
    hosts = [f"{net}.{i}" for i in range(1, 255)]
    alive: list[str] = []
    with ThreadPoolExecutor(max_workers=128) as pool:
        for ip, ok in zip(hosts, pool.map(_ping_alive, hosts)):
            if ok:
                alive.append(ip)
    if not alive:
        return {"ok": True, "net": net, "alive": [], "found": [],
                "message": "网段内没有存活主机", "elapsed": round(_time.time() - started, 1)}

    candidates: list[tuple[str, int, str]] = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        for ip, ok in zip(alive, pool.map(lambda h: _probe_port(h, 5555, 0.5) is not None, alive)):
            if ok:
                candidates.append((ip, 5555, "adb5555"))

    if deep:
        own = accessible_pc_ip()
        targets = [h for h in alive if h != own and not h.endswith(".1")]
        low = int(lo) if lo else WIRELESS_PORT_RANGE[0]
        high = int(hi) if hi else WIRELESS_PORT_RANGE[1]
        for host in targets:
            ports = wireless_scan_ports(host, low, high).get("ports", [])
            for port in ports:
                if (host, port, "wireless") not in candidates:
                    candidates.append((host, port, "wireless"))

    found: list[dict] = []
    connected: list[str] = []
    for ip, port, kind in candidates:
        result = wireless_connect(ip, port)
        item = {"ip": ip, "port": port, "kind": kind, "connected": bool(result.get("ok")),
                "serial": result.get("serial", f"{ip}:{port}"),
                "message": result.get("message") or result.get("error", "")}
        found.append(item)
        if item["connected"]:
            connected.append(item["serial"])
    return {"ok": True, "net": net, "alive": alive, "found": found,
            "connected": connected, "elapsed": round(_time.time() - started, 1)}


def accessible_stop(serial: str, service: str = ACCESSIBLE_SERVICE) -> dict:
    """关闭单台无障碍通道：先清本地运行标记再做手机侧清理。

    本地标记最先清：即使 adb 不通（USB 已拔）界面也不会卡在"无障碍"上；
    手机侧操作全部尽力而为失败只记 warning 不翻转结果。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    _ACCESSIBLE_RUNTIME.pop(serial, None)
    accessible_kill_reverse(serial)
    session = _ACCESSIBLE_SESSIONS.pop(serial, None)
    if session is not None:
        try:
            session.stop()
        except Exception:
            pass
    removed = accessible_disable(serial, service)
    if not removed.get("ok"):
        return {"ok": True, "serial": serial, "mode": 0, "is_accessible": False,
                "warning": "本地已切回 ADB，手机侧授权未撤回: " + str(removed.get("error") or "")[:200]}
    _ACCESSIBLE_RUNTIME.pop(serial, None)
    return {"ok": True, "serial": serial, "mode": 0, "is_accessible": False}


def accessible_kill_reverse(serial: str) -> dict:
    """清掉该设备全部 adb 反向隧道（对齐汇城 kill_reverse_all_ports）。

    尽力而为：adb 不在/设备离线时直接返回失败，不抛异常。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, out = adb(serial, "reverse", "--remove-all", timeout=15)
    return {"ok": rc == 0, "serial": serial,
            "message": (out or "")[:200] if rc == 0 else (out or "清理反向隧道失败")[:200]}


def accessible_close_by_ip(ip: str = "", serial: str = "") -> dict:
    """按 IP 关闭（对齐汇城 close_accessible_device_serial）。"""
    target = serial.strip() if serial and serial.strip() else ""
    if not target and ip and ip.strip():
        for key, entry in list(_ACCESSIBLE_RUNTIME.items()):
            if entry.get("ip") == ip.strip():
                target = key
                break
    if not target:
        return {"ok": False, "error": "必须指定设备序列号或已登记 IP"}
    stopped = accessible_stop(target)
    payload = dict(stopped)
    payload["ip"] = ip
    return payload


# ---------------------------------------------------------------------------
# P1：HID 模式
# ---------------------------------------------------------------------------

HID_APP = "com.android.xwhid"


def hid_check_installed(serial: str) -> dict:
    """检查 HID 助手是否已安装（对齐汇城 check_hid_app_installed）。"""
    rc, out = adb(serial, "shell", "pm list packages " + HID_APP, timeout=15)
    if rc != 0:
        return {"ok": False, "installed": None, "package": HID_APP, "error": out or "检查 HID 助手失败"}
    installed = f"package:{HID_APP}" in (line.strip() for line in out.splitlines())
    return {"ok": True, "installed": installed, "package": HID_APP}


def hid_install_app(serial: str, apk_path: str = "") -> dict:
    """安装 HID 助手（对齐汇城 install_hid_app）。

    默认从汇城 tools 目录找 xwhid.apk。
    """
    if not apk_path:
        candidates = [
            str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("xwhid.apk") or ""),
            str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("XWHid.apk") or ""),
            str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("hid.apk") or ""),
        ]
        for c in candidates:
            if os.path.exists(c):
                apk_path = c
                break
    if not apk_path or not os.path.exists(apk_path):
        return {"ok": False, "error": "找不到 HID 助手 APK", "searched": "tools/*.apk"}

    rc, out = adb(serial, "install", "-r", "-g", apk_path, timeout=120)
    return {
        "ok": "Success" in out,
        "apk": apk_path,
        "message": out[:300],
    }


def hid_is_mode(serial: str) -> dict:
    """检测是否处于 HID 模式（对齐汇城 is_hid_mode）。

    HID 模式下，电脑会看到一个新的人机接口设备（键盘/鼠标）。
    """
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    rc, ps = adb(serial, "shell", "ps -A", timeout=10)
    if rc != 0 or not ps.strip():
        return {"ok": False, "error": "无法读取设备进程状态", "is_hid": None}
    names = [line.split()[-1] for line in ps.splitlines()[1:] if line.split()]
    running = any(name == HID_APP or name.startswith(HID_APP + ':') for name in names)
    usb = usb_list()
    hid_devs = [d for d in usb.get("devices", []) if d.get("is_hid")]
    # 主机上的 HID 设备无法可靠归属到指定手机；进程存在也不能证明 HID 功能开启。
    return {"ok": True, "device_hid_running": running,
            "pc_hid_devices": len(hid_devs), "is_hid": None,
            "note": "无法从主机 HID 列表确定该手机的 HID 状态"}


def hid_switch_mode(serial: str, enable: bool = True) -> dict:
    """切换 HID 模式（对齐汇城 switch_hid_mode）。"""
    if not serial or not serial.strip():
        return {"ok": False, "error": "必须指定设备序列号"}
    if enable:
        chk = hid_check_installed(serial)
        if not chk.get("ok"):
            return {"ok": False, "error": chk.get("error", "无法检查 HID 助手")}
        if not chk.get("installed"):
            return {"ok": False, "error": "HID 助手未安装"}
        cmd = f"am startservice {HID_APP}/.HidService"
    else:
        cmd = f"am force-stop {HID_APP}"
    rc, out = adb(serial, "shell", cmd, timeout=15)
    if rc != 0 or re.search(r'(?i)\b(error|exception|denied|not found|failed)\b', out):
        return {"ok": False, "error": out[:200] or "设备拒绝 HID 命令"}
    if enable and not re.search(r'(?i)starting service', out):
        return {"ok": False, "error": "设备未确认服务启动"}
    return {"ok": True, "enable": enable, "confirmed": False,
            "message": "命令已提交，HID 实际状态未确认"}


def hid_remove_driver() -> dict:
    """卸载电脑端 HID 驱动（对齐汇城 remove_hid_driver）。

    Windows 下用 pnputil 找并删除相关驱动。
    """
    try:
        r = subprocess.run(
            ["pnputil", "/enum-drivers"],
            capture_output=True, text=True, timeout=30,
            encoding="gbk", errors="ignore",
        )
        # 找含 hid / xw 的 oem*.inf
        found = []
        cur = {}
        for ln in (r.stdout or "").splitlines():
            m = re.match(r"\s*发布名称:\s*(oem\d+\.inf)", ln) or re.match(
                r"\s*Published Name:\s*(oem\d+\.inf)", ln
            )
            if m:
                cur["inf"] = m.group(1)
            if "原始名称" in ln or "Original Name" in ln:
                cur["orig"] = ln.split(":")[-1].strip()
            if "提供程序名称" in ln or "Provider Name" in ln:
                cur["provider"] = ln.split(":")[-1].strip()
                if cur.get("inf") and re.search(r"hid|xw|huicheng", str(cur), re.I):
                    found.append(cur)
                cur = {}
        removed = []
        for d in found:
            rr = subprocess.run(
                ["pnputil", "/delete-driver", d["inf"], "/uninstall", "/force"],
                capture_output=True, text=True, timeout=60,
                encoding="gbk", errors="ignore",
            )
            removed.append({"inf": d["inf"], "ok": rr.returncode == 0})
        return {"ok": True, "found": len(found), "removed": removed}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# P1：ADB 模式
# ---------------------------------------------------------------------------


def adb_check_device(serial: str) -> dict:
    """检测 ADB 是否可用（对齐汇城 check_adb_device）。"""
    rc, out = adb(serial, "get-state", timeout=10)
    state = out.strip()
    return {
        "ok": state == "device",
        "state": state,
        "serial": serial,
    }


def adb_switch_mode(serial: str, enable: bool = True) -> dict:
    """切换 ADB 开关（对齐汇城 switch_adb_mode）。

    开启：adb tcpip 5555 + 保持 usb 配置含 adb
    关闭：adb usb
    """
    if enable:
        shell(serial, "setprop persist.adb.tcp.port 5555", timeout=10)
        rc, out = adb(serial, "tcpip", "5555", timeout=20)
        return {"ok": "restarting" in out.lower(), "message": out[:200]}
    rc, out = adb(serial, "usb", timeout=20)
    return {"ok": True, "message": out[:200]}


# ---------------------------------------------------------------------------
# P1：USB 设备列表（电脑端）
# ---------------------------------------------------------------------------


def usb_list() -> dict:
    """列出电脑上的 USB 设备（对齐汇城 get_usb_devices / has_hid_devices）。

    用 PowerShell 的 Get-PnpDevice，能拿到 HID 键盘/鼠标。
    """
    try:
        ps = (
            "Get-PnpDevice -PresentOnly | "
            "Where-Object { $_.InstanceId -like 'USB*' -or $_.InstanceId -like 'HID*' } | "
            "Select-Object -First 200 Class, FriendlyName, InstanceId, Status | "
            "ConvertTo-Json -Compress"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=40,
            encoding="utf-8", errors="ignore",
        )
        raw = r.stdout.strip()
        if not raw:
            return {"ok": True, "devices": [], "count": 0}
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        devices = []
        for d in data:
            cls = (d.get("Class") or "").lower()
            name = d.get("FriendlyName") or ""
            devices.append({
                "class": d.get("Class"),
                "name": name,
                "instance": d.get("InstanceId"),
                "status": d.get("Status"),
                "is_hid": "hid" in cls or "keyboard" in cls or "mouse" in cls,
            })
        return {"ok": True, "devices": devices, "count": len(devices)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "devices": [], "error": str(e)}


def usb_has_hid() -> dict:
    """是否有 HID 设备（对齐汇城 has_hid_devices）。"""
    r = usb_list()
    hid = [d for d in r.get("devices", []) if d.get("is_hid")]
    return {"ok": True, "has_hid": len(hid) > 0, "count": len(hid), "devices": hid}


# ---------------------------------------------------------------------------
# P2：网络 / IP
# ---------------------------------------------------------------------------


def device_ip(serial: str) -> dict:
    """取设备 IP（对齐汇城 get_serial_ip）。

    优先 wlan0 的 inet 地址，其次 rmnet（移动数据）。
    """
    import ipaddress
    if not serial or not serial.strip():
        return {"ok": False, "serial": serial, "ip": "", "error": "必须指定设备序列号"}
    rc, out = adb(serial, "shell", "ip addr show wlan0", timeout=12)
    if rc != 0:
        return {"ok": False, "serial": serial, "ip": "", "error": "读取设备网络地址失败"}
    match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", out)
    if not match:
        rc, out = adb(serial, "shell", "ip route", timeout=10)
        if rc != 0:
            return {"ok": False, "serial": serial, "ip": "", "error": "读取设备路由失败"}
        for line in out.splitlines():
            if re.search(r"\bdev\s+wlan0\b", line):
                match = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", line)
                if match:
                    break
    try:
        ip = str(ipaddress.IPv4Address(match.group(1))) if match else ''
    except ipaddress.AddressValueError:
        ip = ''
    return {"ok": bool(ip), "serial": serial, "ip": ip}


def ip_to_serial(ip: str) -> dict:
    """按 IP 找序列号（对齐汇城 get_ip_serial）。"""
    rc, out = adb("", "devices", timeout=15)
    matches = []
    for ln in out.splitlines():
        if ip in ln:
            parts = ln.split()
            if parts:
                matches.append(parts[0])
    # 也从已连接的网络设备里找
    for serial in matches:
        info = device_ip(serial)
        if info.get("ip") == ip:
            return {"ok": True, "ip": ip, "serial": serial, "matched": True}
    return {"ok": bool(matches), "ip": ip, "serials": matches}


def arp_table() -> dict:
    """读 ARP 表（对齐汇城 get_arp_out）——用于发现局域网设备。"""
    try:
        r = subprocess.run(
            ["arp", "-a"],
            capture_output=True, text=True, timeout=20,
            encoding="gbk", errors="ignore",
        )
        entries = []
        for ln in (r.stdout or "").splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f\-]{17})", ln, re.I)
            if m:
                entries.append({"ip": m.group(1), "mac": m.group(2)})
        return {"ok": True, "entries": entries, "count": len(entries)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "entries": []}


def scan_subnet(net_prefix: str, port: int = 5555, timeout: float = 0.12) -> dict:
    """扫一个 /24 网段（对齐汇城 push_scan_ips）。

    汇城是「三次扫描」策略，这里保留原始能力，由上层控制次数。
    """
    found = []
    for host in range(1, 255):
        ip = f"{net_prefix}.{host}"
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, port)) == 0:
                found.append({"ip": ip, "port": port})
        finally:
            s.close()
    return {"ok": True, "net": net_prefix, "port": port, "found": found, "count": len(found)}


def external_ip() -> dict:
    """外网 IP（对齐汇城 get_external_ip）。"""
    try:
        import urllib.request

        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=8) as r:
            data = json.loads(r.read().decode())
            return {"ok": True, "ip": data.get("ip", "")}
    except Exception:
        # 兜底：UDP connect 拿出口 IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                return {"ok": True, "ip": s.getsockname()[0]}
            finally:
                s.close()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


def ws_port() -> dict:
    """本机 WS 端口（对齐汇城 get_ws_port）。"""
    return {"ok": True, "port": 8765, "note": "FishFleet 固定 8765"}


# ---------------------------------------------------------------------------
# P3：其他设备操作
# ---------------------------------------------------------------------------


def set_wallpaper(serial: str, image_path: str = "") -> dict:
    """设为壁纸（对齐汇城 wallpapers_device）。

    不传图片就用设备上前一张截图。
    """
    if not image_path:
        return {"ok": False, "error": "请先保存截图并指定图片路径"}

    if not os.path.exists(image_path):
        return {"ok": False, "error": "图片不存在"}

    remote = "/sdcard/ff_wallpaper.png"
    rc, out = adb(serial, "push", image_path, remote, timeout=60)
    if rc != 0 or re.search(r'(?i)\b(error|failed|denied)\b', out):
        return {"ok": False, "message": out[:200] or "推送失败"}

    rc, out2 = adb(serial, "shell", f"am start -a android.intent.action.ATTACH_DATA "
                   f"-d file://{remote} -t image/png "
                   f"-e mimeType image/png -n com.android.settings/.AttachStorage", timeout=20)
    if rc != 0 or re.search(r'(?i)\b(error|exception|failed|denied)\b', out2):
        return {"ok": False, "pushed": remote, "message": out2[:200] or "无法打开壁纸设置"}
    return {"ok": True, "pushed": remote, "confirmed": False,
            "message": "图片已推送，壁纸是否设置仍需在手机确认"}


def pull_screenshot(serial: str, out_path: str = "") -> dict:
    """导出截图到本地（对齐汇城 download_image）。"""
    if not out_path:
        d = Path(tempfile.gettempdir()) / "fishfleet_shots"
        d.mkdir(exist_ok=True)
        safe_serial = re.sub(r'[^A-Za-z0-9_.-]', '_', serial)[:80] or 'device'
        out_path = str(d / f"{safe_serial}_{time.time_ns()}.png")

    r = subprocess.run(
        [adb_path(), "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=40,
    )
    if r.returncode != 0 or not r.stdout or not r.stdout.startswith(b'\x89PNG\r\n\x1a\n') or len(r.stdout) < 100:
        return {"ok": False, "error": "截图失败或返回数据不是 PNG"}
    Path(out_path).write_bytes(r.stdout)
    return {
        "ok": True,
        "path": out_path,
        "bytes": len(r.stdout),
        "md5": hashlib.md5(r.stdout).hexdigest(),
    }


def file_md5(path: str) -> dict:
    """算文件 MD5（对齐汇城 get_file_md5）。"""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return {"ok": True, "path": path, "md5": h.hexdigest(), "size": os.path.getsize(path)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# P3：动作脚本 / Auto.js
# ---------------------------------------------------------------------------


def run_action_script(serial: str, script: str) -> dict:
    """跑一段动作脚本（对齐汇城 action_act）。

    格式：每行一个动作
        tap 540 960
        swipe 540 1500 540 500 300
        key back
        text 你好
        sleep 1000
    """
    results = []
    for ln in script.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        op = parts[0].lower()
        try:
            if op == "tap" and len(parts) >= 3:
                shell(serial, f"input tap {parts[1]} {parts[2]}")
            elif op == "swipe" and len(parts) >= 5:
                ms = parts[5] if len(parts) > 5 else "300"
                shell(serial, f"input swipe {parts[1]} {parts[2]} {parts[3]} {parts[4]} {ms}")
            elif op == "key" and len(parts) >= 2:
                shell(serial, f"input keyevent {parts[1]}")
            elif op == "text":
                t = ln[ln.index("text") + 4:].strip()
                shell(serial, f"input text '{t}'")
            elif op == "sleep" and len(parts) >= 2:
                time.sleep(int(parts[1]) / 1000.0)
            else:
                results.append({"line": ln, "ok": False, "error": "未知动作"})
                continue
            results.append({"line": ln, "ok": True})
        except Exception as e:  # noqa: BLE001
            results.append({"line": ln, "ok": False, "error": str(e)})
    return {
        "ok": all(r["ok"] for r in results),
        "total": len(results),
        "results": results,
    }


AUTOJS_PKG = "org.autojs.autojs"


def autojs_run(serial: str, script_path: str) -> dict:
    """跑 Auto.js 脚本（对齐汇城 exec_autojs）。"""
    chk = shell(serial, f"pm list packages {AUTOJS_PKG}", timeout=15)
    if AUTOJS_PKG not in chk:
        return {"ok": False, "error": "Auto.js 未安装", "package": AUTOJS_PKG}

    # 推脚本
    remote = f"/sdcard/脚本/ff_{int(time.time())}.js"
    rc, out = adb(serial, "push", script_path, remote, timeout=60)
    if "error" in out.lower():
        return {"ok": False, "error": out[:200]}

    # 启动执行
    out2 = shell(
        serial,
        f"am start -n {AUTOJS_PKG}/org.autojs.autojs.external.open.RunIntentActivity "
        f"-d file://{remote} -t application/x-javascript",
        timeout=20,
    )
    return {"ok": True, "script": remote, "message": out2[:200]}


def autojs_stop(serial: str) -> dict:
    """停止 Auto.js（对齐汇城 stop_autojs）。"""
    out = shell(serial, f"am force-stop {AUTOJS_PKG}", timeout=15)
    return {"ok": True, "message": out[:100]}


# ---------------------------------------------------------------------------
# P3：配置
# ---------------------------------------------------------------------------

_SYS_CONFIG_CACHE: dict[str, tuple[float, dict]] = {}


def read_sys_config() -> dict:
    """读汇城的 sys_config.toml（对齐汇城 read_sys_config）。"""
    p = str(__import__("fishfleet.paths", fromlist=["huicheng_config"]).huicheng_config() or "")
    if not os.path.exists(p):
        return {"ok": False, "error": "找不到 sys_config.toml"}
    try:
        txt = open(p, encoding="utf-8", errors="ignore").read()
        cfg: dict[str, Any] = {}
        section = ""
        for ln in txt.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("[") and ln.endswith("]"):
                section = ln[1:-1]
                cfg.setdefault(section, {})
                continue
            if "=" in ln:
                k, v = ln.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if section:
                    cfg[section][k] = v
                else:
                    cfg[k] = v
        return {"ok": True, "config": cfg, "path": p}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def system_config() -> dict:
    """本机系统信息（对齐汇城 get_system_config）。"""
    import platform

    info = {
        "ok": True,
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "adb": adb_path(),
        "adb_exists": os.path.exists(adb_path()) if not adb_path() == "adb" else True,
    }
    try:
        info["external_ip"] = external_ip().get("ip", "")
    except Exception:
        pass
    return info

def scan_three_pass(net_prefix: str = "", port: int = 5555,
                    rounds: int = 3, timeout: float = 0.10) -> dict:
    """三次扫描策略（对齐汇城「三次扫描」）。

    汇城的做法：
      第一次扫描 → 找到一部分
      第二次扫描 → 补漏（第一次超时的）
      第三次扫描 → 再补一次
    每轮用更长的超时，最后汇总去重。

    同时会检查端口占用（对齐汇城「端口被占用」）。
    """
    # 端口占用检测
    port_used = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port_used = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
    except Exception:
        pass

    nets = [net_prefix] if net_prefix else _candidate_nets()

    found: dict[str, dict] = {}
    round_stats = []

    for r in range(rounds):
        # 每轮超时递增：0.10 / 0.20 / 0.30
        t = timeout * (r + 1)
        new_in_round = 0
        for net in nets:
            res = scan_subnet(net, port, t)
            for item in res.get("found", []):
                key = f"{item['ip']}:{item['port']}"
                if key not in found:
                    found[key] = {**item, "round": r + 1}
                    new_in_round += 1
        round_stats.append({
            "round": r + 1,
            "timeout": t,
            "new": new_in_round,
            "total": len(found),
        })
        if new_in_round == 0 and r >= 1:
            # 连续两轮没新增，提前结束
            break

    return {
        "ok": True,
        "nets": nets,
        "port": port,
        "port_used_locally": port_used,
        "rounds": round_stats,
        "found": list(found.values()),
        "count": len(found),
    }


def _candidate_nets() -> list[str]:
    """候选网段（复用 server.py 的逻辑，但独立实现避免循环导入）。"""
    nets = set()
    # 本机 IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            nets.add(".".join(ip.split(".")[:3]))
        finally:
            s.close()
    except Exception:
        pass
    # USB 网络共享常见段
    for fixed in ("192.168.42", "192.168.137", "192.168.49", "10.42.0"):
        nets.add(fixed)
    return sorted(nets)

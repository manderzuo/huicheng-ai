"""设备扩展 API —— 把 device_ext 的能力暴露成 HTTP 接口。

路由命名刻意对齐汇城的命令名，方便对照：
    POST /api/ext/uninstall_apk
    POST /api/ext/get_apk_info
    ...
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core import device_ext as dx

router = APIRouter(prefix="/api/ext", tags=["ext"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class SerialReq(BaseModel):
    serial: str


class PackageReq(BaseModel):
    serial: str
    package: str


class UninstallReq(BaseModel):
    serial: str
    package: str
    keep_data: bool = False


class ModeReq(BaseModel):
    serial: str
    mode: str


class ReconnectReq(BaseModel):
    serial: str
    port: int = 5555


class SerialBoolReq(BaseModel):
    serial: str
    enable: bool = True


class AccessibleInstallReq(BaseModel):
    serial: str
    apk_path: str = ""


class AccessibleSwitchReq(BaseModel):
    serial: str = ""
    enable: bool = True
    src: int = 0
    ip: str = ""
    o_addr: str = ""
    use_soft_encoder: bool = True


class AccessibleCloseReq(BaseModel):
    ip: str = ""
    serial: str = ""


class WirelessAddReq(BaseModel):
    target: str
    code: str = ""
    lo: int = 0
    hi: int = 0


class WirelessScanReq(BaseModel):
    ip: str
    lo: int = 0
    hi: int = 0


class WirelessPairReq(BaseModel):
    ip: str
    port: int
    code: str


class WirelessConnectReq(BaseModel):
    ip: str
    port: int


class WifiScanReq(BaseModel):
    net: str = ""
    deep: bool = False
    lo: int = 0
    hi: int = 0


class WallpaperReq(BaseModel):
    serial: str
    image_path: str = ""


class ShotReq(BaseModel):
    serial: str
    out_path: str = ""


class ScriptReq(BaseModel):
    serial: str
    script: str


class AutojsReq(BaseModel):
    serial: str
    script_path: str


class ScanReq(BaseModel):
    net: str
    port: int = 5555


class IpReq(BaseModel):
    ip: str


class Md5Req(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# P0：应用 / 屏幕 / 设备
# ---------------------------------------------------------------------------


@router.post("/uninstall_apk")
async def api_uninstall_apk(req: UninstallReq) -> JSONResponse:
    return JSONResponse(dx.uninstall_apk(req.serial, req.package, req.keep_data))


@router.post("/get_apk_info")
async def api_get_apk_info(req: PackageReq) -> JSONResponse:
    return JSONResponse(dx.get_apk_info(req.serial, req.package))


@router.post("/get_apk_list")
async def api_get_apk_list(req: SerialReq, third_party: bool = False) -> JSONResponse:
    apps = dx.list_packages(req.serial, third_party)
    return JSONResponse({"ok": True, "count": len(apps), "apps": apps})


@router.post("/get_density")
async def api_get_density(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.get_density, req.serial))


@router.post("/get_size")
async def api_get_size(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.get_size, req.serial))


@router.post("/get_device_info")
async def api_get_device_info(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.get_device_info, req.serial))


@router.post("/get_device_mode")
async def api_get_device_mode(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.get_device_mode, req.serial))


@router.post("/switch_device_mode")
async def api_switch_device_mode(req: ModeReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.switch_device_mode, req.serial, req.mode))


@router.get("/usb_modes")
async def api_usb_modes() -> JSONResponse:
    return JSONResponse({"ok": True, "modes": dx.USB_MODES})


# ---------------------------------------------------------------------------
# P0：连接管理
# ---------------------------------------------------------------------------


@router.post("/reconnect_device")
async def api_reconnect_device(req: ReconnectReq) -> JSONResponse:
    return JSONResponse(dx.reconnect_device(req.serial, req.port))


@router.post("/restart_adb")
async def api_restart_adb() -> JSONResponse:
    return JSONResponse(dx.restart_adb())


@router.post("/close_device")
async def api_close_device(req: SerialReq) -> JSONResponse:
    return JSONResponse(dx.close_device(req.serial))


@router.post("/device_disconnect")
async def api_device_disconnect(req: SerialReq) -> JSONResponse:
    return JSONResponse(dx.disconnect_device(req.serial))


# ---------------------------------------------------------------------------
# P1：无障碍
# ---------------------------------------------------------------------------


@router.post("/device_is_accessible")
async def api_device_is_accessible(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.accessible_is_granted, req.serial))


@router.post("/get_accessible_devices")
async def api_get_accessible_devices(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.accessible_list, req.serial))


async def _reset_stream(serial: str) -> None:
    """切换通道前先停掉旧投屏会话，避免 ADB 会话与无障碍会话并存。"""
    try:
        from . import server as _srv
        worker = _srv._streams.get(serial)
        if worker is not None:
            await asyncio.to_thread(worker.stop)
    except Exception:
        pass


@router.post("/switch_accessible_mode")
async def api_switch_accessible_mode(req: AccessibleSwitchReq) -> JSONResponse:
    serial = (req.serial or "").strip()
    if not serial:
        return JSONResponse({"ok": False, "error": "必须指定设备序列号"})
    if req.enable:
        # 已在无障碍通道上就直接返回：先停流再重建会把好端端的会话打死
        # （ADB 关闭时重建必然失败，等于亲手掐断画面）。
        try:
            from . import server as _srv2
            _w = _srv2._streams.get(serial)
            _rt = dx.accessible_runtime(serial) or {}
            if (_rt.get("is_accessible") and _w is not None and _w.running
                    and getattr(_w, "accessible_mode", False)):
                return JSONResponse({"ok": True, "serial": serial, "mode": 3,
                                       "is_accessible": True,
                                       "port": _rt.get("port", 0), "skipped": True,
                                       "note": "已在无障碍通道，无需重建"})
        except Exception:
            pass
        await _reset_stream(serial)
        return JSONResponse(await asyncio.to_thread(
            dx.accessible_start, serial, req.src, req.ip, req.o_addr, req.use_soft_encoder,
        ))
    await _reset_stream(serial)
    stopped = await asyncio.to_thread(dx.accessible_stop, serial)
    try:
        await asyncio.to_thread(dx.adb_switch_mode, serial, True)
    except Exception:
        pass
    return JSONResponse(stopped)


@router.post("/install_accessible")
async def api_install_accessible(req: AccessibleInstallReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.accessible_install_app, req.serial, req.apk_path))


@router.post("/add_accessible")
async def api_add_accessible(req: AccessibleSwitchReq) -> JSONResponse:
    return JSONResponse(dx.accessible_add(req.serial, req.ip, req.o_addr))


@router.post("/start_accessible")
async def api_start_accessible(req: AccessibleSwitchReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(
        dx.accessible_start, req.serial, req.src, req.ip, req.o_addr, req.use_soft_encoder,
    ))


@router.post("/close_accessible_device_serial")
async def api_close_accessible_device_serial(req: AccessibleCloseReq) -> JSONResponse:
    if (req.serial or "").strip():
        await _reset_stream(req.serial.strip())
    return JSONResponse(await asyncio.to_thread(dx.accessible_close_by_ip, req.ip, req.serial))


@router.get("/accessible_devices")
async def api_accessible_devices() -> JSONResponse:
    return JSONResponse(dx.accessible_list_devices())


@router.get("/accessible_dialog")
async def api_accessible_dialog() -> JSONResponse:
    return JSONResponse({"ok": True, **dx.ACCESSIBLE_DIALOG})


@router.get("/accessible_failure_text")
async def api_accessible_failure_text() -> JSONResponse:
    return JSONResponse({"ok": True, "texts": dx.ACCESSIBLE_FAILURE_TEXT})


# ---------------------------------------------------------------------------
# P1：无线调试（adb pair / connect / 端口扫描）
# ---------------------------------------------------------------------------


@router.post("/wireless_add")
async def api_wireless_add(req: WirelessAddReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(
        dx.wireless_add, req.target, req.code, req.lo, req.hi,
    ))


@router.post("/wireless_scan")
async def api_wireless_scan(req: WirelessScanReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(
        dx.wireless_scan_ports, req.ip, req.lo, req.hi,
    ))


@router.post("/wireless_pair")
async def api_wireless_pair(req: WirelessPairReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(
        dx.wireless_pair, req.ip, req.port, req.code,
    ))


@router.post("/wireless_connect")
async def api_wireless_connect(req: WirelessConnectReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(
        dx.wireless_connect, req.ip, req.port,
    ))


@router.post("/wifi_scan")
async def api_wifi_scan(req: WifiScanReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(
        dx.wifi_scan, req.net, req.deep, req.lo, req.hi,
    ))


# ---------------------------------------------------------------------------
# P1：HID
# ---------------------------------------------------------------------------


@router.post("/check_hid_app_installed")
async def api_check_hid_app_installed(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.hid_check_installed, req.serial))


class HidInstallReq(BaseModel):
    serial: str
    apk_path: str = ""


@router.post("/install_hid_app")
async def api_install_hid_app(req: HidInstallReq) -> JSONResponse:
    return JSONResponse(dx.hid_install_app(req.serial, req.apk_path))


@router.post("/is_hid_mode")
async def api_is_hid_mode(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.hid_is_mode, req.serial))


@router.post("/switch_hid_mode")
async def api_switch_hid_mode(req: SerialBoolReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.hid_switch_mode, req.serial, req.enable))


@router.post("/remove_hid_driver")
async def api_remove_hid_driver() -> JSONResponse:
    return JSONResponse(dx.hid_remove_driver())


# ---------------------------------------------------------------------------
# P1：ADB 模式 / USB
# ---------------------------------------------------------------------------


@router.post("/check_adb_device")
async def api_check_adb_device(req: SerialReq) -> JSONResponse:
    return JSONResponse(dx.adb_check_device(req.serial))


@router.post("/switch_adb_mode")
async def api_switch_adb_mode(req: SerialBoolReq) -> JSONResponse:
    return JSONResponse(dx.adb_switch_mode(req.serial, req.enable))


@router.post("/get_usb_devices")
async def api_get_usb_devices() -> JSONResponse:
    return JSONResponse(dx.usb_list())


@router.post("/has_hid_devices")
async def api_has_hid_devices() -> JSONResponse:
    return JSONResponse(dx.usb_has_hid())


# ---------------------------------------------------------------------------
# P2：网络 / IP
# ---------------------------------------------------------------------------


@router.post("/get_serial_ip")
async def api_get_serial_ip(req: SerialReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.device_ip, req.serial))


@router.post("/get_ip_serial")
async def api_get_ip_serial(req: IpReq) -> JSONResponse:
    return JSONResponse(dx.ip_to_serial(req.ip))


@router.get("/get_arp_out")
async def api_get_arp_out() -> JSONResponse:
    return JSONResponse(dx.arp_table())


@router.post("/push_scan_ips")
async def api_push_scan_ips(req: ScanReq) -> JSONResponse:
    return JSONResponse(dx.scan_subnet(req.net, req.port))


@router.get("/get_external_ip")
async def api_get_external_ip() -> JSONResponse:
    return JSONResponse(dx.external_ip())


class Scan3Req(BaseModel):
    net: str = ""
    port: int = 5555
    rounds: int = 3


@router.post("/otg_scanning_three")
async def api_otg_scanning_three(req: Scan3Req) -> JSONResponse:
    """三次扫描（对齐汇城 otg_scanning 的三轮策略）。"""
    return JSONResponse(dx.scan_three_pass(req.net, req.port, req.rounds))


@router.get("/get_ws_port")
async def api_get_ws_port() -> JSONResponse:
    return JSONResponse(dx.ws_port())


# ---------------------------------------------------------------------------
# P3：其他
# ---------------------------------------------------------------------------


@router.post("/wallpapers_device")
async def api_wallpapers_device(req: WallpaperReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.set_wallpaper, req.serial, req.image_path))


@router.post("/download_image")
async def api_download_image(req: ShotReq) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(dx.pull_screenshot, req.serial, req.out_path))


@router.post("/get_file_md5")
async def api_get_file_md5(req: Md5Req) -> JSONResponse:
    return JSONResponse(dx.file_md5(req.path))


@router.post("/action_act")
async def api_action_act(req: ScriptReq) -> JSONResponse:
    return JSONResponse(dx.run_action_script(req.serial, req.script))


@router.post("/exec_autojs")
async def api_exec_autojs(req: AutojsReq) -> JSONResponse:
    return JSONResponse(dx.autojs_run(req.serial, req.script_path))


@router.post("/stop_autojs")
async def api_stop_autojs(req: SerialReq) -> JSONResponse:
    return JSONResponse(dx.autojs_stop(req.serial))


@router.post("/read_sys_config")
async def api_read_sys_config() -> JSONResponse:
    return JSONResponse(dx.read_sys_config())


@router.get("/get_system_config")
async def api_get_system_config() -> JSONResponse:
    return JSONResponse(dx.system_config())


@router.get("/capabilities")
async def api_capabilities() -> JSONResponse:
    """列出本模块提供的全部能力（给前端/CLI 自省用）。"""
    return JSONResponse({
        "ok": True,
        "groups": {
            "P0_应用屏幕设备": [
                "uninstall_apk", "get_apk_info", "get_apk_list", "get_density",
                "get_size", "get_device_info", "get_device_mode", "switch_device_mode",
            ],
            "P0_连接": [
                "reconnect_device", "restart_adb", "close_device", "device_disconnect",
            ],
            "P1_无障碍": [
                "device_is_accessible", "get_accessible_devices", "switch_accessible_mode",
                "install_accessible", "add_accessible", "start_accessible",
                "close_accessible_device_serial", "accessible_devices",
                "accessible_dialog", "accessible_failure_text",
            ],
            "P1_无线调试": [
                "wireless_add", "wireless_scan", "wireless_pair", "wireless_connect", "wifi_scan",
            ],
            "P1_HID": [
                "check_hid_app_installed", "install_hid_app", "is_hid_mode",
                "switch_hid_mode", "remove_hid_driver",
            ],
            "P1_ADB_USB": [
                "check_adb_device", "switch_adb_mode", "get_usb_devices", "has_hid_devices",
            ],
            "P2_网络": [
                "get_serial_ip", "get_ip_serial", "get_arp_out", "push_scan_ips",
                "get_external_ip", "get_ws_port",
            ],
            "P3_其他": [
                "wallpapers_device", "download_image", "get_file_md5", "action_act",
                "exec_autojs", "stop_autojs", "read_sys_config", "get_system_config",
            ],
        },
        "count": 46,
    })

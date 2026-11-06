"""OTG TCP 端口与局域网设备发现。所有设备变更仅由显式请求触发。"""
from __future__ import annotations

import asyncio
import ipaddress
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..core.device import get_manager
from ..core import device_ext

router = APIRouter(prefix='/api/otg', tags=['otg'])
MAX_PROBES = 500

class PortRequest(BaseModel):
    port: int = Field(ge=1, le=65535)
    serials: list[str] = Field(default_factory=list, max_length=256)

class Range(BaseModel):
    ip1: str
    ip2: str
    port: int = Field(ge=1, le=65535)
    port2: int | None = Field(default=None, ge=1, le=65535)

class ScanRequest(BaseModel):
    ranges: list[Range] = Field(min_length=1, max_length=500)


def expand_ranges(ranges: list[Range]) -> list[tuple[str, int]]:
    result = set()
    for row in ranges:
        try:
            start, end = ipaddress.IPv4Address(row.ip1), ipaddress.IPv4Address(row.ip2)
        except ValueError as exc:
            raise ValueError('IPv4 地址无效') from exc
        last_port = row.port2 if row.port2 is not None else row.port
        if int(end) < int(start) or last_port < row.port:
            raise ValueError('结束地址或端口不能小于起始值')
        count = (int(end) - int(start) + 1) * (last_port - row.port + 1)
        if count > MAX_PROBES or len(result) + count > MAX_PROBES:
            raise ValueError(f'单次扫描最多 {MAX_PROBES} 个地址与端口组合')
        for value in range(int(start), int(end) + 1):
            addr = ipaddress.IPv4Address(value)
            if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise ValueError('不能扫描回环、链路本地、多播或保留地址')
            for port in range(row.port, last_port + 1):
                result.add((str(addr), port))
    return sorted(result, key=lambda item: (int(ipaddress.IPv4Address(item[0])), item[1]))


def _enable_port(port: int, requested: list[str]) -> dict:
    manager = get_manager()
    devices = dict(manager.adb.devices())
    selected = list(dict.fromkeys(requested)) if requested else [s for s, state in devices.items() if ':' not in s and state == 'device']
    results = []
    for serial in selected:
        state = devices.get(serial)
        if ':' in serial or not re.fullmatch(r'[A-Za-z0-9._-]+', serial):
            results.append({'serial': serial, 'ok': False, 'message': '不是 USB ADB 设备'})
        elif state != 'device':
            results.append({'serial': serial, 'ok': False, 'message': f'设备未在线或未授权：{state or "未找到"}'})
        else:
            code, out, err = manager.adb._run(['tcpip', str(port)], timeout=20, serial=serial)
            message = (out + err).strip()
            ok = code == 0 and bool(re.search(r'restarting in TCP mode port:\s*' + str(port) + r'\b', message, re.I))
            results.append({'serial': serial, 'ok': ok, 'message': message or f'adb 返回码 {code}'})
    return {'ok': bool(results) and all(x['ok'] for x in results), 'results': results,
            'success': sum(x['ok'] for x in results), 'total': len(results)}

@router.post('/enable-port')
async def enable_port(req: PortRequest):
    return await asyncio.to_thread(_enable_port, req.port, req.serials)

async def _probe(ip: str, port: int, sem: asyncio.Semaphore):
    async with sem:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=0.35)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return {'ip': ip, 'port': port}
        except (OSError, asyncio.TimeoutError):
            return None

@router.post('/scan-ranges')
async def scan_ranges(req: ScanRequest):
    try:
        targets = expand_ranges(req.ranges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sem = asyncio.Semaphore(64)
    results = await asyncio.gather(*(_probe(ip, port, sem) for ip, port in targets))
    found = [x for x in results if x]
    return {'ok': True, 'tested': len(targets), 'found': found}

@router.post('/device-ips')
async def device_ips():
    manager = get_manager()
    devices = await asyncio.to_thread(manager.adb.devices)
    async def get_ip(serial):
        if ':' in serial:
            try:
                ip, port = serial.rsplit(':', 1)
                return {'ok': True, 'serial': serial, 'ip': str(ipaddress.IPv4Address(ip)), 'port': int(port)}
            except ValueError:
                return {'ok': False, 'serial': serial, 'error': '网络地址无效'}
        return await asyncio.to_thread(device_ext.device_ip, serial)
    rows = await asyncio.gather(*(get_ip(s) for s, state in devices if state == 'device'))
    return {'ok': True, 'devices': rows}

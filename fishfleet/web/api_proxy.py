"""LLM 独立 HTTP 代理：纯配置验证、只读系统检测及统一 urllib 路由。"""
from __future__ import annotations

import ipaddress
import os
import re
import urllib.parse
import urllib.request

KINDS = ("auto", "video")
DEFAULT_PROXY = {"enabled": False, "url": "http://127.0.0.1:7890", "targets": ["auto", "video"]}


def validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("kind 仅允许 auto 或 video")
    return kind


def validate_proxy_url(value: str, *, allow_empty: bool = False) -> str:
    """所有错误均为固定文本，绝不回显可能含凭据的输入。"""
    error = "代理 URL 必须是无凭据、查询、片段或路径的 http/https host:port"
    if not isinstance(value, str):
        raise ValueError(error)
    if value == "" and allow_empty:
        return ""
    if not value or re.search(r"[\s\\\x00-\x1f\x7f]", value) or "?" in value or "#" in value:
        raise ValueError(error)
    try:
        parsed = urllib.parse.urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in ("http", "https") or not host or port is None
                or not 1 <= port <= 65535 or "@" in parsed.netloc
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError(error)
        if ":" in host:
            ipaddress.IPv6Address(host)
            if "%" in host:
                raise ValueError(error)
        else:
            # IDNA 域名与 IPv4；不接受百分号编码、空标签及非法 host 字符。
            ascii_host = host.encode("idna").decode("ascii")
            if len(ascii_host) > 253 or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in ascii_host.rstrip(".").split(".")
            ):
                raise ValueError(error)
        # 要求显式的十进制端口，拒绝 +80、空端口等宽松解析。
        if not re.search(r":\d+$", parsed.netloc, flags=re.ASCII):
            raise ValueError(error)
    except (ValueError, UnicodeError):
        raise ValueError(error) from None
    return value


def validate_proxy(proxy: dict) -> dict:
    if not isinstance(proxy, dict):
        raise ValueError("proxy 必须是对象")
    enabled = proxy.get("enabled", False)
    targets = proxy.get("targets", list(KINDS))
    if type(enabled) is not bool:
        raise ValueError("proxy.enabled 必须是布尔值")
    if not isinstance(targets, list) or any(not isinstance(k, str) or k not in KINDS for k in targets):
        raise ValueError("proxy.targets 仅允许 auto/video 数组")
    url = validate_proxy_url(proxy.get("url", DEFAULT_PROXY["url"]), allow_empty=not enabled)
    return {"enabled": enabled, "url": url, "targets": list(dict.fromkeys(targets))}


def public_proxy(wholeconfig: dict) -> dict:
    """旧配置缺省可用；手工损坏的历史配置不回显不安全 URL。"""
    try:
        return validate_proxy(wholeconfig.get("proxy", {}))
    except ValueError:
        return validate_proxy({})


def _windows_proxy() -> str:
    """只读取 HKCU 两个静态代理值，不读取或执行 PAC。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if enabled != 1:
                return ""
            value = winreg.QueryValueEx(key, "ProxyServer")[0]
            return value if isinstance(value, str) else ""
    except (ImportError, OSError):
        return ""


def detect_proxy() -> dict:
    """返回按优先级排列的静态候选，不保存、不连接、不改变启用状态。"""
    candidates = []

    def add(source, value):
        try:
            url = validate_proxy_url(value)
        except ValueError:
            return
        item = {"source": source, "url": url}
        if item not in candidates:
            candidates.append(item)

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = os.environ.get(name)
        if value:
            add("env:" + name, value)
    registry = _windows_proxy()
    for entry in registry.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            protocol, entry = entry.split("=", 1)
            if protocol.strip().lower() not in ("http", "https"):
                continue
            source = "windows:" + protocol.strip().lower()
        else:
            source = "windows:ProxyServer"
        entry = entry.strip()
        add(source, entry if "://" in entry else "http://" + entry)
    first = candidates[0] if candidates else {"source": "", "url": ""}
    return {"source": first["source"], "candidates": candidates, "url": first["url"]}


class _ExplicitProxyHandler(urllib.request.ProxyHandler):
    """独立配置不受 NO_PROXY/系统 bypass 影响，也不改进程全局设置。"""

    def proxy_open(self, req, proxy, type):
        parsed = urllib.parse.urlsplit(proxy)
        original_type = req.type
        req.set_proxy(parsed.netloc, parsed.scheme)
        if original_type == parsed.scheme or original_type == "https":
            return None
        return self.parent.open(req, timeout=req.timeout)


def open_api(request, kind: str, wholeconfig: dict, timeout):
    """命中独立代理时显式代理两种协议；任何失败均不回退直连。"""
    validate_kind(kind)
    proxy = validate_proxy(wholeconfig.get("proxy", {}))
    try:
        if proxy["enabled"] and kind in proxy["targets"]:
            handler = _ExplicitProxyHandler({"http": proxy["url"], "https": proxy["url"]})
            return urllib.request.build_opener(handler).open(request, timeout=timeout)
        return urllib.request.urlopen(request, timeout=timeout)
    except Exception:
        # urllib 的异常可能包含代理 URL、上游 URL 或认证信息。
        raise RuntimeError("API 请求失败，请检查 API 与代理设置") from None

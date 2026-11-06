"""LLM 专用 httpx 代理快照；不修改进程环境或全局 httpx 行为。"""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
import urllib.request
from pathlib import Path
from typing import Any

from ..web.api_proxy import validate_kind, validate_proxy

LLM_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "llm.json"


def _configured_proxy() -> dict:
    """只使用根 data/llm.json 的 proxy，不合并 API/model 或核心配置。"""
    try:
        with LLM_CONFIG_PATH.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError):
        return validate_proxy({})
    if not isinstance(data, dict):
        return validate_proxy({})
    return validate_proxy(data.get("proxy", {}))


def _environment_patterns(proxy_info: dict[str, str]) -> dict[str, str | None]:
    """对应 httpx 0.28 的环境映射，仅额外识别带括号的 IPv6 字面量。"""
    patterns: dict[str, str | None] = {}
    for scheme in ("http", "https", "all"):
        value = proxy_info.get(scheme)
        if value:
            patterns[f"{scheme}://"] = value if "://" in value else f"http://{value}"

    for hostname in proxy_info.get("no", "").split(","):
        hostname = hostname.strip()
        if hostname == "*":
            return {}
        if not hostname:
            continue
        if "://" in hostname:
            patterns[hostname] = None
            continue
        # 不对域名/任意方括号文本做宽松 strip，避免改变其他 bypass 语义。
        if hostname.startswith("[") and hostname.endswith("]"):
            candidate = hostname[1:-1]
            try:
                ipaddress.IPv6Address(candidate)
            except ValueError:
                pass
            else:
                hostname = candidate
        address = hostname.split("/")[0]
        try:
            ipaddress.IPv4Address(address)
        except ValueError:
            try:
                ipaddress.IPv6Address(address)
            except ValueError:
                pattern = f"all://{hostname}" if hostname.lower() == "localhost" else f"all://*{hostname}"
            else:
                pattern = f"all://[{hostname}]"
        else:
            pattern = f"all://{hostname}"
        patterns[pattern] = None
    return patterns


def _environment_verify() -> bool | ssl.SSLContext:
    # trust_env=False 同时禁用了 httpx 的证书变量读取，因此显式保留原优先级。
    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    return True  # 无证书变量时仍使用 httpx 自己的 certifi 默认值。


def httpx_proxy_kwargs(kind: str = "auto") -> dict[str, Any]:
    """为单个同步 Client 生成 kwargs；返回的 mounts 由该 Client 负责关闭。"""
    import httpx

    validate_kind(kind)
    proxy = _configured_proxy()
    verify = _environment_verify()
    kwargs: dict[str, Any] = {"trust_env": False, "verify": verify}
    if proxy["enabled"] and kind in proxy["targets"]:
        # 显式独立代理不能被 NO_PROXY（包括 *）绕过，也不回退环境代理。
        kwargs["proxy"] = proxy["url"]
        return kwargs

    snapshot = dict(urllib.request.getproxies())
    mounts: dict[str, Any] = {}
    try:
        for pattern, url in _environment_patterns(snapshot).items():
            mounts[pattern] = None if url is None else httpx.HTTPTransport(
                proxy=url, verify=verify, trust_env=False,
            )
    except Exception:
        for transport in mounts.values():
            if transport is not None:
                transport.close()
        raise
    kwargs["mounts"] = mounts
    return kwargs

# -*- coding: utf-8 -*-
"""汇城群控AI版 · 网络与安装环境检测

2026-11-07 主人要求「有什么快捷检测工具没」：
    客户机装不上时，需要**一眼看出**是网络问题、源问题还是别的，
    而不是靠猜。这个脚本检查：
        1. 各 pip 镜像的连通性与速度
        2. pypi.org 是否拖慢（--extra-index-url 的老坑）
        3. nodejs 源 / npm 源
        4. 本机是否设置了 HTTP_PROXY（会影响下载）
        5. 已装组件的状态
        6. 安装路径长度
    结果同时打印并写入 `网络检测.txt`。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent if HERE.name in ('engine', 'scripts') else HERE
RUNTIME = BASE / 'runtime'

OUT: list[str] = []


def W(s: str = '') -> None:
    OUT.append(s)
    try:
        print(s, flush=True)
    except Exception:
        pass


def probe(name: str, url: str, timeout: float = 8.0, read: int = 512):
    """测一次 HTTP 访问，返回 (耗时秒, 状态, 说明)。"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (FishFleet diagnostics)'})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(read)
            return time.time() - t0, 'OK', 'HTTP %s' % r.status
    except Exception as exc:
        return None, 'FAIL', str(exc)[:60]


def main() -> int:
    W('=' * 64)
    W('  汇城群控AI版 · 网络与安装环境检测')
    W('  时间: %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    W('  目录: %s' % BASE)
    W('=' * 64)
    W()

    # ---------- 1. 代理 ----------
    W('[1] 代理设置（会影响所有下载）')
    found_proxy = False
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        v = os.environ.get(k)
        if v:
            W('  %-12s = %s' % (k, v))
            found_proxy = True
    if not found_proxy:
        W('  未设置环境变量代理（直连）')
    W('  说明：如果代理不可用，pip 会一直等超时 —— 这是常见卡点。')
    W()

    # ---------- 2. pip 镜像 ----------
    W('[2] pip 镜像测速（取最快的）')
    mirrors = [
        ('清华 TUNA', 'https://pypi.tuna.tsinghua.edu.cn/simple/pip/'),
        ('阿里云', 'https://mirrors.aliyun.com/pypi/simple/pip/'),
        ('腾讯云', 'https://mirrors.cloud.tencent.com/pypi/simple/pip/'),
        ('中科大', 'https://pypi.mirrors.ustc.edu.cn/simple/pip/'),
        ('华为云', 'https://repo.huaweicloud.com/repository/pypi/simple/pip/'),
        ('官方 PyPI', 'https://pypi.org/simple/pip/'),
    ]
    results = []
    for name, url in mirrors:
        cost, state, detail = probe(name, url)
        if cost is not None:
            W('  %-12s OK    %6.2f 秒   %s' % (name, cost, detail))
            results.append((cost, name, url))
        else:
            W('  %-12s FAIL           %s' % (name, detail))
    W()
    if results:
        results.sort()
        best = results[0]
        W('  >>> 最快: %s（%.2f 秒）' % (best[1], best[0]))
        slow = [r for r in results if r[1] == '官方 PyPI']
        if slow and slow[0][0] > best[0] * 3:
            W('  >>> 官方 PyPI 比国内源慢 %.1f 倍，不要用它当备用源'
              % (slow[0][0] / best[0]))
    else:
        W('  >>> 所有镜像都不通！检查网络/防火墙/代理')
    W()

    # ---------- 3. Node / npm ----------
    W('[3] Node.js 与 npm 源')
    for name, url in (
        ('nodejs 官方', 'https://nodejs.org/dist/index.json'),
        ('npmmirror', 'https://registry.npmmirror.com/'),
    ):
        cost, state, detail = probe(name, url)
        if cost is not None:
            W('  %-14s OK    %6.2f 秒' % (name, cost))
        else:
            W('  %-14s FAIL           %s' % (name, detail))
    W()

    # ---------- 4. 已装组件 ----------
    W('[4] 已安装的组件')
    checks = [
        ('Python', RUNTIME / 'python' / 'python.exe'),
        ('pip 依赖', RUNTIME / 'python' / 'Lib' / 'site-packages' / 'fastapi'),
        ('Node.js', RUNTIME / 'node' / 'node.exe'),
        ('mobile-mcp', RUNTIME / 'mobile-mcp' / 'start-mobile-mcp.cjs'),
        ('adb', BASE / 'bin' / 'adb.exe'),
    ]
    for name, p in checks:
        W('  %-12s %s' % (name, 'OK   ' + str(p) if p.exists() else 'MISSING'))
    st = RUNTIME / '.state.json'
    if st.exists():
        try:
            d = json.loads(st.read_text(encoding='utf-8'))
            W()
            W('  上次安装状态（.state.json）:')
            for k, v in d.items():
                ok = v.get('ok') if isinstance(v, dict) else v
                W('    %-14s %s' % (k, '成功' if ok else '失败'))
        except Exception:
            pass
    W()

    # ---------- 5. 路径长度 ----------
    W('[5] 安装路径长度（Windows 上限 260）')
    deepest = len(str(BASE)) + len(r'\runtime\python\Lib\site-packages\opencv_python.libs\abcdefghijklmnop.pyd')
    W('  当前目录: %d 字符' % len(str(BASE)))
    W('  最深路径: 约 %d 字符' % deepest)
    if deepest > 240:
        W('  >>> 太长！把文件夹移到 D:\\AI\\ 之类的短路径')
    else:
        W('  OK')
    W()

    # ---------- 6. 磁盘 ----------
    W('[6] 磁盘空间')
    try:
        import shutil
        total, used, free = shutil.disk_usage(str(BASE))
        W('  剩余: %.1f GB（安装需要约 1.5 GB）' % (free / 1073741824))
        if free < 2 * 1073741824:
            W('  >>> 空间不足，建议先清理')
    except Exception as exc:
        W('  读取失败: %s' % exc)
    W()

    # ---------- 结论 ----------
    W('=' * 64)
    W('  结论')
    W('=' * 64)
    if not results:
        W('  [问题] 所有 pip 镜像都不通 —— 检查网络、防火墙、代理')
    elif results[0][0] > 5:
        W('  [慢] 最快的镜像也要 %.1f 秒，网络很差，安装会很久' % results[0][0])
    elif found_proxy:
        W('  [注意] 设置了代理 %s，确认它是通的（否则会反复超时）'
          % os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'))
    else:
        W('  [正常] 网络和镜像都没问题')
    W()
    W('  建议：安装时如果看到「依赖 下载 xxx」，说明在正常工作，耐心等。')
    W('        只有 15 分钟完全没有新行，才是真卡住。')
    W('=' * 64)

    # 写文件
    try:
        p = BASE / '网络检测.txt'
        p.write_text('\n'.join(OUT), encoding='utf-8')
        print()
        print('结果已保存: %s' % p)
    except Exception as exc:
        print('保存失败: %s' % exc)
    return 0


if __name__ == '__main__':
    try:
        socket.setdefaulttimeout(10)
    except Exception:
        pass
    raise SystemExit(main())

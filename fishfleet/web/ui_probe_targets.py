# -*- coding: utf-8 -*-
"""界面坐标注册表 + 按机型自适应解析（dp 锚定版）。

为什么用 dp 而不是像素/百分比（2026-11-04 主人提问后重做）：

    Android 应用布局用的是 **dp**，不是像素：
        1 dp = dpi / 160 像素
    "界面长什么样"取决于**屏幕有多少 dp**，而不是有多少像素。

    实测对照（宽 dp 才是关键）：
        1080×1920 @420  ->  411 × 731 dp   （当前这 6 台）
        1440×3200 @560  ->  411 × 914 dp   （2K 屏，宽 dp 一样！）
        1080×2400 @420  ->  411 × 914 dp   （20:9，宽 dp 一样！）
        720×1280  @320  ->  360 × 640 dp   （小屏）
        1080×1920 @360  ->  480 × 853 dp   （手动调过"显示大小"）

    所以：
      · 分辨率/DPI 同比例变化（1080@420 -> 1440@560）：dp 不变 -> 坐标**零影响**；
      · 只变长宽比（20:9）：宽 dp 不变 -> **X 零影响**，Y 必须从顶部/底部锚定；
      · 手动改显示大小：宽 dp 变（411->480）-> 全部偏移，
        但用 dp 表达时 `px = dp × dpi/160` 会自动跟着变，仍然自适应。

    因此坐标一律写成「锚定哪条边 + 距该边多少 dp」，解析时按当前 dpi 换算回像素。
    百分比（ratio）只作为最后兜底保留。

用法：
    from .ui_probe_targets import resolve_point, screen_profile
    x, y = resolve_point(serial, 'douyin.home.search')
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
PROFILE_DIR = os.path.join(_ROOT, 'fishfleet', 'ui_library', 'profiles')

# ---------------------------------------------------------------- 坐标注册表
# anchor 说明（dx/dy 都是"元素中心"到该边的 dp 距离）：
#   top_left     : dx 从左边量, dy 从屏幕顶（含状态栏）量
#   top_right    : dx 从右边量, dy 从屏幕顶量
#   top_center   : 水平居中，dy 从屏幕顶量
#   bottom_left  : dx 从左边量, dy 从屏幕底（含导航栏）量
#   bottom_right : dx 从右边量, dy 从屏幕底量
#   bottom_center: 水平居中，dy 从屏幕底量
# checkable=False：子页面坐标（搜索输入页/拍摄页/编辑器等），
#   一键自检只覆盖各 App 首页，这些键不参检，只做解析用。
TARGETS = {
    # ---- 抖音 ----
    'douyin.home.search': {
        'app': 'com.ss.android.ugc.aweme', 'page': '首页',
        'anchor': 'top_right', 'dp': (26.3, 69.0),
        'ratio': (0.935, 0.089),          # 兜底用
        'expect': {'text_in': ['搜索'], 'clickable': True},
        'page_markers': ['首页', '朋友', '消息'],
        'note': '首页右上角放大镜；无障碍树里是混淆串，只能按位置点',
    },
    'douyin.home.tab_home': {
        'app': 'com.ss.android.ugc.aweme', 'page': '首页',
        'anchor': 'bottom_left', 'dp': (41.1, 72.8),
        'ratio': (0.074, 0.972),
        'expect': {'text_in': ['首页', '首頁'], 'clickable': True},
        'page_markers': ['首页', '朋友', '消息'],
        'note': '底部导航第一个 Tab',
    },
    # ---- 小红书 ----
    'xiaohongshu.home.search': {
        'app': 'com.xingin.xhs', 'page': '首页',
        'anchor': 'top_right', 'dp': (28.0, 64.0),
        'ratio': (0.87, 0.06),
        'expect': {'text_in': ['搜索'], 'clickable': True},
        'page_markers': ['首页', '购物', '消息', '我'],
        'note': '首页右上角搜索',
    },
    # ---- B站 ----
    'bilibili.home.publish': {
        'app': 'tv.danmaku.bili', 'page': '首页',
        'anchor': 'bottom_center', 'dp': (0.0, 72.0),
        'ratio': (0.5, 0.922),
        'expect': {'text_in': ['发布', '投稿'], 'clickable': True},
        'page_markers': ['首页', '动态', '我的'],
        'note': '首页底部中间「发布」按钮',
    },
    # ---- TikTok ----
    'tiktok.home.search': {
        'app': 'com.zhiliaoapp.musically', 'page': '首页',
        'anchor': 'top_right', 'dp': (30.0, 60.0),
        'ratio': (0.88, 0.055),
        'expect': {'text_in': ['搜索', 'Search'], 'clickable': True},
        'page_markers': ['Home', 'Friends', 'Profile', '首页'],
        'note': '首页右上角搜索',
    },
    # ---- Facebook ----
    'facebook.home.search': {
        'app': 'com.facebook.katana', 'page': '首页',
        'anchor': 'top_right', 'dp': (30.0, 72.0),
        'ratio': (0.88, 0.075),
        'expect': {'text_in': ['搜尋', '搜索', 'Search'], 'clickable': True},
        'page_markers': ['首頁', 'Home', '通知', 'Notifications'],
        'note': '首页顶部搜索',
    },
    'facebook.composer.entry': {
        'app': 'com.facebook.katana', 'page': '首页',
        'anchor': 'top_right', 'dp': (115.0, 72.0),
        'ratio': (0.72, 0.075),
        'expect': {'text_in': ['建立', '建立貼文', 'Create'], 'clickable': True},
        'page_markers': ['首頁', 'Home', '通知', 'Notifications'],
        'note': '首页顶部「+／建立」入口',
    },
    # ---- TikTok 搜索体系（搜索结果页/输入页通用，省 3 次 dump） ----
    'tiktok.search.box': {
        'app': 'com.zhiliaoapp.musically', 'page': '搜索',
        'anchor': 'top_left', 'dp': (204.6, 82.7),
        'ratio': (0.497, 0.113),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '搜索输入框中心（手机7实测 537,217）',
    },
    'tiktok.search.clear': {
        'app': 'com.zhiliaoapp.musically', 'page': '搜索',
        'anchor': 'top_right', 'dp': (67.4, 82.7),
        'ratio': (0.836, 0.113),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '搜索框内「清除」按钮（手机7实测 903,217）',
    },
    'tiktok.search.submit': {
        'app': 'com.zhiliaoapp.musically', 'page': '搜索',
        'anchor': 'top_right', 'dp': (25.9, 82.3),
        'ratio': (0.937, 0.113),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '右侧「搜索」提交按钮（手机7实测 1012,216）',
    },
    # ---- TikTok 发布链路 ----
    'tiktok.publish.create': {
        'app': 'com.zhiliaoapp.musically', 'page': '首页',
        'anchor': 'bottom_center', 'dp': (0.0, 72.8),
        'ratio': (0.5, 0.9),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '首页底部「创建」（手机7实测 540,1729）',
    },
    'tiktok.publish.album_entry': {
        'app': 'com.zhiliaoapp.musically', 'page': '拍摄页',
        'anchor': 'bottom_right', 'dp': (78.1, 154.3),
        'ratio': (0.81, 0.789),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '拍摄页右侧相册入口（手机7实测 875,1515）',
    },
    'tiktok.publish.caption': {
        'app': 'com.zhiliaoapp.musically', 'page': '发布页',
        'anchor': 'top_left', 'dp': (127.6, 140.2),
        'ratio': (0.31, 0.192),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '发布页描述框（手机7实测 335,368）',
    },
    'tiktok.publish.next_fallback': {
        'app': 'com.zhiliaoapp.musically', 'page': '相册/编辑页',
        'anchor': 'bottom_right', 'dp': (110.5, 68.6),
        'ratio': (0.731, 0.906),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '右下角「下一步」兜底（手机7实测 790,1740）',
    },
    'tiktok.publish.topic_dismiss': {
        'app': 'com.zhiliaoapp.musically', 'page': '发布页',
        'anchor': 'top_center', 'dp': (0.0, 419.0),
        'ratio': (0.5, 0.573),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '话题标签面板出现时点空白收起（手机7实测 540,1100）',
    },
    # ---- B站 ----
    'bilibili.dm.icon': {
        'app': 'tv.danmaku.bili', 'page': '用户主页',
        'anchor': 'top_right', 'dp': (86.9, 48.4),
        'ratio': (0.789, 0.066),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '用户主页右上角私信图标（1080x1920实测 852,127）',
    },
    'bilibili.search.bar_fallback': {
        'app': 'tv.danmaku.bili', 'page': '首页',
        'anchor': 'top_left', 'dp': (192.8, 63.6),
        'ratio': (0.469, 0.087),
        'expect': {'text_in': ['搜索'], 'clickable': True},
        'page_markers': ['首页', '动态', '我的'],
        'checkable': False,
        'note': '首页搜索框兜底（1080x1920实测 506,167）',
    },
    'bilibili.search.mask_dismiss': {
        'app': 'tv.danmaku.bili', 'page': '搜索结果页',
        'anchor': 'bottom_center', 'dp': (0.0, 160.0),
        'ratio': (0.5, 0.781),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '排序弹层点遮罩关闭（1080x1920实测 540,1500）',
    },
    # ---- Facebook 发布/私信兜底 ----
    'facebook.composer.gallery_fallback': {
        'app': 'com.facebook.katana', 'page': '编辑器',
        'anchor': 'bottom_left', 'dp': (132.6, 84.2),
        'ratio': (0.322, 0.885),
        'expect': {'text_in': ['图库', '圖庫'], 'clickable': True},
        'page_markers': [],
        'checkable': False,
        'note': '编辑器「图库」按钮兜底（手机8实测 348,1699）',
    },
    'facebook.composer.first_cell': {
        'app': 'com.facebook.katana', 'page': '图库',
        'anchor': 'top_left', 'dp': (68.6, 243.8),
        'ratio': (0.167, 0.333),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '图库第一格兜底（1080x1920实测 180,640）',
    },
    'facebook.dm.input_wake': {
        'app': 'com.facebook.katana', 'page': '私信会话',
        'anchor': 'bottom_center', 'dp': (0.0, 83.8),
        'ratio': (0.5, 0.885),
        'expect': {}, 'page_markers': [],
        'checkable': False,
        'note': '输入框未渲染时点屏幕下部唤起（手机8实测 540,1700）',
    },
}

# 基准机型（只用于报告里做对比，不参与计算）
BASELINE = {'width': 1080, 'height': 1920, 'dpi': 420,
            'status_bar': 63, 'nav_bar': 126,
            'dp_w': 411.4, 'dp_h': 731.4}


# ---------------------------------------------------------------- 设备几何
def _adb(serial: str, cmd: str, timeout: float = 15.0) -> str:
    adb = os.environ.get('ADB_PATH') or __import__('fishfleet.paths', fromlist=['adb_path']).adb_path()
    try:
        p = subprocess.run([adb, '-s', serial, 'shell', cmd],
                           capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='ignore')
        return (p.stdout or '') + (p.stderr or '')
    except Exception:
        return ''


def screen_profile(serial: str) -> dict:
    """读真机几何：像素、DPI、系统栏、以及**dp 尺寸**（真正决定布局的东西）。"""
    geo = {'serial': serial, 'width': BASELINE['width'], 'height': BASELINE['height'],
           'dpi': BASELINE['dpi'],
           'status_bar': BASELINE['status_bar'], 'nav_bar': BASELINE['nav_bar'],
           'source': 'default'}
    out = _adb(serial, 'wm size')
    m = re.search(r'Override size:\s*(\d+)x(\d+)', out) or \
        re.search(r'Physical size:\s*(\d+)x(\d+)', out)
    if m:
        geo['width'], geo['height'] = int(m.group(1)), int(m.group(2))
        geo['source'] = 'wm'
        geo['size_override'] = 'Override size' in out
    out = _adb(serial, 'wm density')
    m = re.search(r'Override density:\s*(\d+)', out) or \
        re.search(r'Physical density:\s*(\d+)', out)
    if m:
        geo['dpi'] = int(m.group(1))
        geo['density_override'] = 'Override density' in out
    out = _adb(serial, 'dumpsys window displays')
    m = re.search(r'stableInsets.*?top=(\d+)', out, re.S)
    if m:
        geo['status_bar'] = int(m.group(1))
    m2 = re.search(r'stableInsets.*?bottom=(\d+)', out, re.S)
    geo['nav_bar'] = int(m2.group(1)) if m2 else int(round(geo['height'] * 0.0656))
    geo['content_h'] = max(1, geo['height'] - geo['status_bar'] - geo['nav_bar'])
    # ---- 关键：dp 换算 ----
    ppd = geo['dpi'] / 160.0
    geo['px_per_dp'] = round(ppd, 4)
    geo['dp_w'] = round(geo['width'] / ppd, 1)
    geo['dp_h'] = round(geo['height'] / ppd, 1)
    geo['content_dp_h'] = round(geo['content_h'] / ppd, 1)
    geo['status_dp'] = round(geo['status_bar'] / ppd, 1)
    geo['nav_dp'] = round(geo['nav_bar'] / ppd, 1)
    geo['scale'] = round(geo['width'] / BASELINE['width'], 4)
    geo['dpi_scale'] = round(geo['dpi'] / BASELINE['dpi'], 4)
    # dp 家族：宽度 dp 相同就可以共用同一套坐标
    geo['dp_family'] = 'w%d' % int(round(geo['dp_w'] / 10.0) * 10)
    return geo


def device_key(geo: dict) -> str:
    """机型 profile 的键：品牌_型号_宽dp族_高dp。"""
    serial = geo.get('serial') or ''
    model, brand = '', ''
    out = _adb(serial, 'getprop ro.product.model')
    if out.strip():
        model = re.sub(r'[^A-Za-z0-9._-]', '', out.strip().splitlines()[0])
    out = _adb(serial, 'getprop ro.product.brand')
    if out.strip():
        brand = re.sub(r'[^A-Za-z0-9._-]', '', out.strip().splitlines()[0]).lower()
    if not model:
        model = re.sub(r'[^A-Za-z0-9._-]', '', serial) or 'unknown'
    return '%s_%s_%s_%dx%d' % (brand or 'unk', model,
                               geo.get('dp_family', 'w0'),
                               geo.get('width', 0), geo.get('height', 0))


# ---------------------------------------------------------------- profile 读写
def _profile_path(key: str) -> str:
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return os.path.join(PROFILE_DIR, key + '.json')


def load_profile(key: str) -> dict:
    try:
        return json.loads(open(_profile_path(key), encoding='utf-8').read())
    except Exception:
        return {}


def save_profile(key: str, data: dict) -> str:
    p = _profile_path(key)
    tmp = p + '.tmp'
    data = dict(data)
    data['updated_at'] = time.time()
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p

# ---------------------------------------------------------------- 机群覆盖
# 自愈修好后主人可以选「覆盖到所有手机」：写这里，所有机型都吃这套 dp。
# 单机档案（profiles/<device_key>.json）优先级更高，所以「仅本机」不会被它盖掉。
def fleet_path() -> str:
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return os.path.join(PROFILE_DIR, '_fleet.json')


def load_fleet() -> dict:
    try:
        return json.loads(open(fleet_path(), encoding='utf-8').read())
    except Exception:
        return {}


def save_fleet(data: dict) -> str:
    p = fleet_path()
    tmp = p + '.tmp'
    data = dict(data)
    data['updated_at'] = time.time()
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def promote_keys(dp_map: dict, scope: str = 'all') -> dict:
    """把若干 key 的 dp 写进机群覆盖（scope='all'）或丢弃（scope='this'）。"""
    keys = dict(dp_map or {})
    if scope != 'all':
        return {'ok': True, 'scope': scope, 'written': {}}
    fleet = load_fleet()
    offs = dict(fleet.get('dp_offsets') or {})
    for k, v in keys.items():
        offs[k] = [round(float(v[0]), 1), round(float(v[1]), 1)]
    fleet['dp_offsets'] = offs
    save_fleet(fleet)
    return {'ok': True, 'scope': 'all', 'written': keys}



# ---------------------------------------------------------------- 坐标解析
def anchor_to_point(geo: dict, anchor: str, dx_dp: float, dy_dp: float) -> tuple[int, int]:
    """按锚定方式 + dp 距离，换算成本机像素中心点。"""
    ppd = geo['px_per_dp']
    w, h = geo['width'], geo['height']
    if anchor == 'top_left':
        return int(round(dx_dp * ppd)), int(round(dy_dp * ppd))
    if anchor == 'top_right':
        return int(round(w - dx_dp * ppd)), int(round(dy_dp * ppd))
    if anchor == 'top_center':
        return int(round(w / 2.0)), int(round(dy_dp * ppd))
    if anchor == 'bottom_left':
        return int(round(dx_dp * ppd)), int(round(h - dy_dp * ppd))
    if anchor == 'bottom_right':
        return int(round(w - dx_dp * ppd)), int(round(h - dy_dp * ppd))
    if anchor == 'bottom_center':
        return int(round(w / 2.0)), int(round(h - dy_dp * ppd))
    raise ValueError('未知锚定方式：%s' % anchor)


def point_to_anchor(geo: dict, anchor: str, x: int, y: int) -> tuple[float, float]:
    """把实测像素反算成 dp 偏移（自检命中后写 profile 用）。"""
    ppd = geo['px_per_dp']
    w, h = geo['width'], geo['height']
    if anchor == 'top_left':
        return round(x / ppd, 1), round(y / ppd, 1)
    if anchor == 'top_right':
        return round((w - x) / ppd, 1), round(y / ppd, 1)
    if anchor == 'top_center':
        return 0.0, round(y / ppd, 1)
    if anchor == 'bottom_left':
        return round(x / ppd, 1), round((h - y) / ppd, 1)
    if anchor == 'bottom_right':
        return round((w - x) / ppd, 1), round((h - y) / ppd, 1)
    if anchor == 'bottom_center':
        return 0.0, round((h - y) / ppd, 1)
    raise ValueError('未知锚定方式：%s' % anchor)


def resolve_point(serial: str, key: str) -> tuple[int, int]:
    """语义坐标 -> 本机像素。优先级：本机档案实测 dp > 注册表默认 dp > 百分比兜底。"""
    t = TARGETS.get(key)
    if not t:
        raise KeyError('未登记的坐标：%s' % key)
    geo = screen_profile(serial)
    prof = load_profile(device_key(geo))
    saved = (prof.get('dp_offsets') or {}).get(key)
    if saved:
        return anchor_to_point(geo, t['anchor'], float(saved[0]), float(saved[1]))
    fleet = (load_fleet().get('dp_offsets') or {}).get(key)
    if fleet:
        return anchor_to_point(geo, t['anchor'], float(fleet[0]), float(fleet[1]))
    if t.get('dp'):
        return anchor_to_point(geo, t['anchor'], float(t['dp'][0]), float(t['dp'][1]))
    # 最后兜底：内容区百分比（老办法，仅当没有 dp 定义时）
    rx, ry = float(t['ratio'][0]), float(t['ratio'][1])
    return int(round(rx * geo['width'])), int(round(geo['status_bar'] + ry * geo['content_h']))


def ratio_from_point(geo: dict, x: int, y: int) -> tuple[float, float]:
    """兼容旧接口：像素 -> 内容区比例。"""
    rx = x / max(1, geo['width'])
    ry = (y - geo['status_bar']) / max(1, geo['content_h'])
    return round(rx, 4), round(ry, 4)


def sanity_check(geo: dict) -> dict:
    """判断这台机器的 dp 尺寸是否需要重新校准。"""
    out = {'ok': True, 'warnings': []}
    if abs(geo['dp_w'] - BASELINE['dp_w']) > 12:
        out['ok'] = False
        out['warnings'].append('宽度 dp 与基准差 %.0f（%.0f vs %.0f），必须重新自检'
                               % (abs(geo['dp_w'] - BASELINE['dp_w']),
                                  geo['dp_w'], BASELINE['dp_w']))
    if abs(geo['dp_h'] - BASELINE['dp_h']) > 40:
        out['warnings'].append('高度 dp 与基准差 %.0f（%.0f vs %.0f），底部元素会自动跟随'
                               % (abs(geo['dp_h'] - BASELINE['dp_h']),
                                  geo['dp_h'], BASELINE['dp_h']))
    if geo.get('size_override') or geo.get('density_override'):
        out['warnings'].append('检测到被手动改过分辨率/密度，坐标按当前 dp 换算')
    return out

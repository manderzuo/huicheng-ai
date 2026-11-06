"""B站截流：按关键词采集评论线索，写入中转站 leads_bilibili。

技术栈说明（和 B站发布 skill 一致）：
  · 读 UI 树走 Mobile MCP（mobile_list_elements_on_screen）
  · 点击/返回/DeepLink 走 adb（MCP 的 click 会被 B站 某些控件吞掉）
  · 中文输入走 MCP 的 mobile_type_keys（adb input text 不支持中文）
  · aid / up_uid / commenter_uid 从 `dumpsys activity activities` 的 Intent 读，
    这是三个数值型硬锚点，比 UI 文本可靠

只读采集：不评论、不私信、不点赞，任何发送动作都不在本模块。

关键实测（2026-11-01）：
  · 真视频卡有 upuser + duration；广告卡有 ad_marker/ad_tint_frame 且没有 upuser
  · 详情页会弹全屏广告，靠 content-desc="关闭" 关掉
  · 「评论」tab 要认 resource-id 后缀 tab_title，不能只认文字
  · 点评论者昵称进他主页再 BACK，评论区位置保持不变
  · 必须「读一屏 → 点一屏 → 再滚」，否则前面屏的作者已经不在屏幕上，点不到
  · 评论人 == UP 本人时没有联系价值，直接丢弃
"""
from __future__ import annotations

import json
import re
import subprocess
import time

from .mobile_gateway import MobileCallError, call
from .ui_probe_targets import resolve_point as _resolve
from .ui_probe_targets import screen_profile as _profile

SKILL_ID = 'builtin:bilibili-lead'
SKILL = {
    'id': SKILL_ID,
    'name': 'B站截流采集',
    'description': '按关键词采集B站视频评论，记录评论者UID写入中转站，供换号私信。只读采集，不发送。',
}
PACKAGE = 'tv.danmaku.bili'

TIME_RE = re.compile(r'(\d+月\d+日|\d{4}年\d+月|\d+天前|昨天|今天|刚刚|\d+小时前|\d+分钟前)')
BV_RE = re.compile(r'BV[0-9A-Za-z]{10}')
BAD_AUTHOR = re.compile(r'^(\d{1,2}:\d{2}|\d+%|\d+|\d+\.?\d*万|共\d+条回复|\d+条回复|UP|置顶|更多|回复|查看详情|展开|收起|账号已注销|用户已注销|已注销)$')
BAD_CONTENT = re.compile(r'^(广告|互动视频|Android 系统通知|哔哩哔哩通知|盖乐世设置通知|.*轻点两下查看详情|.*￥\d)')
SKIP = {'UP', '置顶', '更多', '点踩', '回复', '热门评论', '按热度', '关闭弹幕',
        '点我发弹幕', '简介', '评论', '发送弹幕', '展开', '收起'}

# 视频必须和「显卡/加密货币挖矿」相关，否则整条跳过。
# 不加这个，「矿难」会采回一堆煤矿事故（山西82人矿难、瓦斯爆炸），实测 74% 是废数据。
# 注意不要放「矿工」——它在煤矿语境更常见，会误放煤矿视频进来。
RELEVANT = re.compile(
    r'(显卡|挖矿|矿卡|矿机|矿场|加密货币|比特币|以太坊|区块链|币圈|虚拟货币|'
    r'算力|矿池|BTC|ETH|挖币|炒币|数字币|矿潮|矿渣|挖过矿|炼过丹|'
    r'40HX|30HX|90HX|P104|P106|P620|V100|RX580|5700XT|BC-160|'
    r'10[5-8]0ti|20[6-8]0|30[5-9]0|40[6-9]0|50[6-9]0|显存|魔改|计算卡)')

# 「矿卡」在矿山语境也常用，这些是矿山卡车/矿业，不是显卡挖矿，要排掉
RELEVANT_EXCLUDE = re.compile(
    r'(电动矿卡|露天矿|矿用卡车|自卸|矿山车|柴油|矿洞|矿井|矿难|瓦斯|煤老板|'
    r'矿工被困|井下)')

# 这些关键词在 B站 上歧义严重，必须做正向过滤，否则会采回完全无关的内容：
#   「矿难」→ 全是煤矿事故，「算力」→ 全是 AI 算力，「矿卡」→ 混进矿山卡车
AMBIGUOUS_KEYWORDS = ('矿难', '算力', '挖矿', '矿卡', '矿机', '矿池', '矿老板', '矿潮')

STOP_CHARS = set('的了是在和有我你他她它们就都而及与着或一个上下来去到说会这那个什怎么')

# B站 搜索页「筛选 → 排序方式」的可选项
SORT_OPTIONS = ('默认排序', '新发布', '播放多', '弹幕多')


def keyword_hit(title: str, keyword: str) -> bool:
    """标题里有没有关键词的痕迹（任一两字组合命中即算）。

    用于采样判断这个词的搜索结果准不准，比 relevant_title 宽松。
    """
    t = title or ''
    parts = [p for p in re.split(r'[\s,，]+', keyword or '') if len(p) >= 2]
    for p in parts:
        for i in range(len(p) - 1):
            if p[i:i + 2] in t:
                return True
    return False


def relevant_title(title: str, keyword: str = '') -> bool:
    """判断视频标题是否值得采。

    · 命中排除词 → 一律不要（矿山卡车、煤矿事故）
    · 关键词歧义（矿难/算力/挖矿/矿卡…）→ 标题必须命中挖矿主题词
    · 关键词不歧义 → 放宽到「任一字命中」：
      「手表维修」的标题常写成「修表」「钟表」「名表」，只认四字会全漏掉
    """
    t = title or ''
    if RELEVANT_EXCLUDE.search(t):
        return False
    if keyword and not any(a in keyword for a in AMBIGUOUS_KEYWORDS):
        parts = [p for p in re.split(r'[\s,，]+', keyword) if len(p) >= 2]
        for p in parts:
            for i in range(len(p) - 1):
                if p[i:i + 2] in t:
                    return True
        # 放宽到单字（跳过虚词，否则「的」「了」会命中一切）
        for ch in keyword:
            if ch.strip() and ch not in STOP_CHARS and ch in t:
                return True
        return False
    if RELEVANT.search(t):
        return True
    if keyword:
        parts = [p for p in re.split(r'[\s,，]+', keyword) if len(p) >= 2]
        if parts and any(p in t for p in parts):
            return True
    return False

ADB_FALLBACK = __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()


class SkillError(Exception):
    pass


# ---------------------------------------------------------------- 基础设施
def _adb_path() -> str:
    try:
        from .server import _adb_path as p
        path = p()
        if path:
            return path
    except Exception:
        pass
    return ADB_FALLBACK


def _adb(serial, cmd, timeout=30):
    subprocess.run([_adb_path(), '-s', serial, 'shell', cmd],
                   capture_output=True, timeout=timeout)


def _tap(serial, x, y):
    _adb(serial, 'input tap %d %d' % (int(x), int(y)))


def _back(serial):
    _adb(serial, 'input keyevent 4')


def _am_start(serial, uri):
    """打开链接。B站自家 scheme 一定钉到 B站包名，避免弹选择器/被别的应用抢。"""
    pin = ' -p tv.danmaku.bili' if str(uri).startswith('bilibili://') else ''
    _adb(serial, 'am start -a android.intent.action.VIEW -d "%s"%s' % (uri, pin),
         timeout=60)


def elements(result):
    """解 MCP 的 JSON 元素列表；坏树不放行。"""
    for item in (result or {}).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            m = re.search(r'\[\s*\{.*\}\s*\]', text, re.S)
            if not m:
                continue
            try:
                value = json.loads(m.group(0))
            except ValueError:
                continue
        if isinstance(value, list) and all(isinstance(e, dict) for e in value):
            return value
    raise SkillError('无法读取当前 UI 树，已停止')


def box(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    if c['width'] <= 0 or c['height'] <= 0:
        return None
    return {'x': int(c['x']), 'y': int(c['y']),
            'w': int(c['width']), 'h': int(c['height'])}


def center(el):
    b = box(el)
    return (b['x'] + b['w'] // 2, b['y'] + b['h'] // 2) if b else None


def rid(el) -> str:
    return str(el.get('identifier') or '').replace(PACKAGE + ':id/', '')


def txt(el) -> str:
    return (el.get('text') or '').strip()


def desc(el) -> str:
    # Mobile MCP 的 JSON 用的是 "label"；"content-desc" 是 uiautomator XML 的叫法。
    # 旧实现只读 "content-desc"，在这套 MCP 下恒为空 —— 于是 comments() 里
    # 「块内必须有 点赞/回复」永远不成立，整轮评论全被丢掉（2026-11-02 实测）。
    return (el.get('label') or el.get('content-desc') or '').strip()


def find_text(elems, needle, exact=False):
    """按 text 或 content-desc 找元素，返回第一个有坐标的。"""
    for e in elems:
        t, d = txt(e), desc(e)
        hit = (t == needle or d == needle) if exact else (needle in t or needle in d)
        if hit and center(e):
            return e
    return None


# ---------------------------------------------------------------- 设备动作
class Runner:
    def __init__(self, task):
        self.task = task
        self.serial = task['serial']
        self.stop = task.get('_stop')

    def log(self, text):
        from . import execution
        execution.log(self.task, text)

    def check(self):
        if self.stop is not None and self.stop.is_set():
            raise SkillError('任务已停止')

    def pause(self, seconds):
        if self.stop is not None and self.stop.wait(seconds):
            self.check()

    def tree(self):
        self.check()
        # 注意：gateway.validate 会把多余参数直接判非法（FIELDS 白名单），
        # 这里只能传工具真正接受的字段；设备由 call 的第三参数指定。
        # 旧代码多塞了 {'device': serial}，导致整条 B 站截流 8 秒就 ValueError。
        return elements(call('mobile_list_elements_on_screen',
                             {'format': 'json'}, self.serial))

    def activity(self) -> str:
        try:
            out = subprocess.run([_adb_path(), '-s', self.serial, 'shell',
                                  'dumpsys activity activities'],
                                 capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return ''
        # 只取 `包名/类名` 里的类名部分。
        # 旧正则 `mResumedActivity:.*?\s([A-Za-z0-9_.]+)\s` 非贪婪，先撞上
        # "ActivityRecord{..." 前的 "u0"（任务用户 id），返回的是 "u0"，
        # 于是永远判成"没进结果页"（2026-11-02 实测：手机明明停在
        # com.bilibili.search2.main.BiliMainSearchActivity）。
        m = re.search(r'mResumedActivity:.*?([A-Za-z][A-Za-z0-9_.]*/[A-Za-z][A-Za-z0-9_.$]+)', out)
        if not m:
            return ''
        return m.group(1).split('/', 1)[1]

    def ids(self) -> dict:
        """从 dumpsys 的 Intent 里取 aid / mid（数值型硬锚点）。"""
        try:
            out = subprocess.run([_adb_path(), '-s', self.serial, 'shell',
                                  'dumpsys activity activities'],
                                 capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return {'aid': '', 'mid': ''}
        res = {'aid': '', 'mid': ''}
        for line in out.splitlines():
            if not res['aid']:
                m = re.search(r'[?&]aid=(\d+)', line)
                if m:
                    res['aid'] = m.group(1)
            if not res['mid']:
                m = re.search(r'[?&]mid=(\d+)', line)
                if m:
                    res['mid'] = m.group(1)
            if res['aid'] and res['mid']:
                break
        return res

    def tap_el(self, el, why=''):
        c = center(el)
        if not c:
            raise SkillError('控件坐标不可用%s' % (('（' + why + '）') if why else ''))
        _tap(self.serial, c[0], c[1])
        return c

    # ---- 各步 ----
    def search(self, keyword):
        """搜索关键词：搜索框输入 + 点「搜索」按钮。

        曾经用 DeepLink（bilibili://search?keyword=…），早期可用，
        但 B站 会记住上次搜索状态，后期所有词都跳视频详情页（实测矿卡/aigc/考研全一样）。
        也不能只按回车：搜索建议会抢输入框。所以输入完立刻点 action_search 按钮。
        """
        for attempt in range(3):
            _adb(self.serial, 'am force-stop ' + PACKAGE)
            self.pause(2)
            _adb(self.serial, 'am start -n %s/.MainActivityV2' % PACKAGE)
            self.pause(6)

            bar = find_text(self.tree(), '搜索')
            if bar:
                c = center(bar)
                _tap(self.serial, c[0], c[1])
            else:
                # 首页搜索框兜底 → 注册表 dp 自适应
                _tap(self.serial, *_resolve(self.serial, 'bilibili.search.bar_fallback'))
            self.pause(3)

            try:
                # 同上：mobile_type_keys 只接受 text/submit，不接受 device。
                call('mobile_type_keys', {'text': keyword}, self.serial)
            except Exception:
                pass
            self.pause(0.8)

            btn = next((e for e in self.tree()
                        if (rid(e) or '').endswith('action_search')), None)
            if btn:
                self.tap_el(btn, '搜索按钮')
            else:
                _adb(self.serial, 'input keyevent 66')
            self.pause(6)

            if 'BiliMainSearchActivity' not in self.activity():
                self.log('搜索第 %d 次没进结果页' % (attempt + 1))
                continue

            for _ in range(15):
                self.pause(2.0)
                if find_text(self.tree(), '加载失败'):
                    return True
                if find_text(self.tree(), '正在加载'):
                    continue
                if self.cards():
                    return True
            return True
        return False

    def apply_sort(self, sort_name):
        """切换搜索结果排序。

        坑：B站 选完排序后弹层不自动关，必须点遮罩；
        不能用 BACK 关，那会把结果页一起退成搜索建议页。
        """
        if not sort_name or sort_name == '默认排序':
            return True

        el = next((e for e in self.tree() if rid(e) == 'filter_icon'), None)
        if not el:
            self.log('找不到筛选按钮，跳过排序')
            return False
        self.tap_el(el, '筛选')
        self.pause(2.0)

        hit = next((e for e in self.tree()
                    if rid(e) == 'content_tv' and (e.get('label') or '') == sort_name),
                   None)
        if not hit:
            self.log('弹层里没有「%s」，跳过' % sort_name)
            _tap(self.serial, *_resolve(self.serial, 'bilibili.search.mask_dismiss'))
            self.pause(1.5)
            return False

        self.tap_el(hit, '排序-' + sort_name)
        self.pause(2.0)
        # 点遮罩关闭弹层（底部居中 dp 自适应）
        _tap(self.serial, *_resolve(self.serial, 'bilibili.search.mask_dismiss'))
        self.pause(2.0)

        for _ in range(10):
            self.pause(0.8)
            if self.cards():
                break
        self.log('排序已切换：%s' % sort_name)
        return True

    def recover_load_failure(self, tries=3):
        """搜索页「加载失败」时点「再试一次」。"""
        for i in range(tries):
            if not find_text(self.tree(), '加载失败'):
                return True
            hit = find_text(self.tree(), '再试一次')
            if not hit:
                return False
            self.log('搜索页加载失败，点「再试一次」（第 %d 次）' % (i + 1))
            self.tap_el(hit, '再试一次')
            self.pause(6)
        return not bool(find_text(self.tree(), '加载失败'))

    def close_ad(self):
        el = find_text(self.tree(), '关闭', exact=True) or find_text(self.tree(), '跳过', exact=True)
        if el:
            self.tap_el(el, '关广告')
            self.pause(2)
            return True
        return False

    # ---- 防呆：页面状态判别 + 还原 -------------------------------------
    def page_state(self):
        """粗分当前页面：results / no_cards / detail / space / home / other。

        与抖音 `page_kind`、小红书 `ensure_state` 对齐：**先判页面再动作**，
        不靠盲按 BACK。2026-11-03 接入 B站（之前只有抖音/小红书有）。
        """
        act = self.activity() or ''
        if 'UnitedBizDetails' in act:
            return 'detail'
        if 'LocalAuthorSpace' in act:
            return 'space'
        if 'BiliMainSearchActivity' in act:
            return 'results' if self.cards() else 'no_cards'
        if 'MainActivityV2' in act:
            return 'home'
        return 'other'

    def ensure_results(self, keyword='', max_steps=6, allow_restart=True):
        """把界面拉回搜索结果页；必要时重搜或重启应用兜底。

        返回 True 表示已确认停在有卡片的搜索结果页。判不出就如实返回 False，
        调用方据此跳过/结束，绝不连续盲按 BACK（会把页面退到桌面）。
        """
        for _ in range(max_steps):
            self.check()
            st = self.page_state()
            if st == 'results':
                return True
            if st in ('detail', 'space'):
                _back(self.serial)
                self.pause(1.2)
                continue
            if st == 'no_cards':
                # 结果页在但卡片没渲染出来：等一拍，仍没有就重搜
                self.pause(1.5)
                if self.cards():
                    return True
                if keyword and self.search(keyword):
                    self.pause(2.0)
                    if self.cards():
                        return True
                continue
            if st == 'home':
                break
            _back(self.serial)
            self.pause(1.0)
        if keyword and self.search(keyword):
            self.pause(2.5)
            if self.cards():
                return True
        if allow_restart:
            self.log('界面未能回到搜索结果页，重启B站应用兜底')
            _adb(self.serial, 'am force-stop ' + PACKAGE)
            self.pause(2)
            _adb(self.serial, 'am start -n %s/.MainActivityV2' % PACKAGE)
            self.pause(6)
            if keyword and self.search(keyword):
                self.pause(2.5)
                return bool(self.cards())
        return False

    def cards(self):
        """只返回真视频卡（有 upuser + duration，无广告标记）。"""
        elems = self.tree()
        # 同上：B站 改版后封面 id 是 sdv_cover，旧版是 cover；两种都收。
        covers = [e for e in elems if rid(e) in ('cover', 'sdv_cover') and box(e)]
        if not covers:
            return []
        ys = sorted(box(c)['y'] for c in covers)
        out = []
        for i, c in enumerate(covers):
            top = box(c)['y']
            bottom = ys[i + 1] if i + 1 < len(ys) else top + 520
            zone = [e for e in elems if top - 20 <= box(e)['y'] < bottom] if all(box(e) for e in elems) else []
            ids = {rid(e) for e in zone}
            if 'ad_marker' in ids or 'ad_tint_frame' in ids or 'adDownloadText' in ids:
                continue
            if 'upuser' not in ids or 'duration' not in ids:
                continue
            title = next((txt(e) for e in zone if rid(e) == 'title' and txt(e)), '')
            up = next((txt(e) for e in zone if rid(e) == 'upuser' and txt(e)), '')
            if title and len(title) >= 5:
                # 把封面元素本身带上：点击时优先用 MCP 的 ref，比裸坐标稳。
                # 2026-11-02 实测：裸点 (700, y+15) 有时落在卡片间隙，没进详情页，
                # 整轮 5 个视频全被当成"没进详情"跳过，采集 0 条。
                out.append({'title': title, 'up': up, 'y': top, 'el': c})
        seen, res = set(), []
        for c in sorted(out, key=lambda x: x['y']):
            if c['title'] in seen:
                continue
            seen.add(c['title'])
            res.append(c)
        return res

    def header(self):
        elems = self.tree()
        cands = [e for e in elems if rid(e) == 'title' and txt(e) and box(e) and 950 < box(e)['y'] < 1600]
        cands.sort(key=lambda e: box(e)['y'])
        title = txt(cands[0])[:80] if cands else ''
        up = ''
        for e in elems:
            if rid(e) in ('author_name', 'uname') and txt(e):
                up = txt(e)[:40]
                break
        return {'title': title, 'up': up}

    def open_comments(self):
        """点「评论」tab，认 tab_title 这个 id。"""
        for _ in range(3):
            elems = self.tree()
            hit = next((e for e in elems
                        if rid(e).endswith('tab_title') and txt(e) == '评论' and center(e)), None)
            if hit is None:
                hit = find_text(elems, '评论', exact=True)
            if hit is None:
                return False
            self.tap_el(hit, '评论tab')
            self.pause(4)
            if find_text(self.tree(), '热门评论') or find_text(self.tree(), '按热度') \
                    or find_text(self.tree(), '按时间'):
                return True
            self.pause(1.5)
        return False

    def comments(self, limit=6):
        """解析当前屏评论：按「时间」节点切块，块内取作者与最长正文。"""
        elems = self.tree()
        rows = []
        for e in elems:
            b = box(e)
            if not b:
                continue
            rows.append({'y': b['y'], 'text': txt(e), 'desc': desc(e)})
        rows.sort(key=lambda r: r['y'])

        blocks, cur = [], []
        for r in rows:
            if TIME_RE.search(r['desc']) or TIME_RE.search(r['text']):
                if cur:
                    blocks.append((cur, r))
                cur = []
            else:
                cur.append(r)

        out = []
        for block, tnode in blocks:
            if len(out) >= limit:
                break
            if any(r['text'] == 'UP' or r['desc'] == 'UP' for r in block):
                continue
            if not any(r['desc'].startswith('点赞') or r['desc'] == '回复' for r in block):
                continue
            author, content = '', ''
            for r in block:
                t, d = r['text'], r['desc']
                if t and t == d and not BAD_AUTHOR.match(t) and 1 < len(t) <= 24 and not author:
                    author = t
                cand = d if len(d) > len(t) else t
                if cand and cand not in SKIP and not cand.startswith('点赞') \
                        and not BAD_AUTHOR.match(cand) and len(cand) > len(content):
                    content = cand
            if not content or len(content) < 4 or BAD_CONTENT.match(content):
                continue
            out.append({'author': author, 'content': content[:300],
                        'time': (tnode['desc'] or tnode['text'])[:40]})
        return out

    def commenter_uid(self, author):
        """点评论者昵称 → 主页 → Intent 取 mid → BACK。"""
        if not author or BAD_AUTHOR.match(author) or BAD_CONTENT.match(author):
            return ''
        elems = self.tree()
        hit = next((e for e in elems
                    if txt(e) == author and desc(e) == author and center(e)), None)
        if hit is None:
            return ''
        self.tap_el(hit, '评论者昵称')
        self.pause(4.5)
        if 'LocalAuthorSpace' not in self.activity():
            _back(self.serial)
            self.pause(2)
            return ''
        uid = self.ids()['mid']
        _back(self.serial)
        self.pause(2.5)
        return uid


# ---------------------------------------------------------------- 主流程
def run(task):
    """task 需要 serial / lead.keywords / lead.videos_per_keyword / lead.comments_per_video"""
    from . import execution
    from . import lead_store

    lead = task.get('lead') or {}
    keywords = [k.strip() for k in (lead.get('keywords') or []) if k.strip()]
    if not keywords:
        raise SkillError('请至少填一个关键词')
    videos_per_kw = int(lead.get('videos_per_keyword') or 5)
    comments_per_video = int(lead.get('comments_per_video') or 10)
    # 采集目标（累计线索条数）：界面只填这一个数字，0 = 不限。
    target_comments = int(lead.get('target_comments') or 0)
    # 有目标时取消"每关键词 N 个视频"的硬上限：没采够就一直采（2026-11-03）。
    if target_comments and videos_per_kw < 500:
        videos_per_kw = 500
    sort_by = (lead.get('sort_by') or lead.get('sort') or '默认排序').strip()
    import time as _t
    try:
        from .time_filter import within_days as _within_days
        _max_age = int(lead.get('max_age_days') or 0)
        _base_ts = float(lead.get('task_created_at') or task.get('created_at') or _t.time())
    except Exception:
        _max_age, _base_ts = 0, _t.time()
    dm_tpl = (lead.get('dm_template') or '').strip() or \
        '你好呀，看到你在「{keyword}」相关视频下留言「{评论}」，我这边刚好懂这块，有问题可以随时问我～'

    r = Runner(task)
    serial = r.serial
    total_videos = 0
    total_leads = 0
    total_comments = 0   # 已读评论条数（采集目标按这个算）

    for kw in keywords:
        r.check()
        if target_comments and total_leads >= target_comments:
            execution.log(task, '已入库 %d 条线索，达到目标 %d，停止' % (total_leads, target_comments))
            break
        execution.log(task, '关键词：%s' % kw)
        if not r.search(kw):
            execution.log(task, '  搜索页没打开，跳过')
            continue
        if not r.recover_load_failure():
            execution.log(task, '  搜索页一直加载失败（可能接口限流），跳过')
            continue
        r.apply_sort(sort_by)

        # 自动判断这个词要不要开过滤：采样标题，看有多少含关键词。
        # 含得少 → 掺了账号历史推荐，必须过滤；含得多 → 过滤只会误伤。
        # 歧义词无条件开过滤（它们的采样也会命中，光看比例会被骗）。
        kw_filter = True
        _ambig = any(a in kw for a in AMBIGUOUS_KEYWORDS)
        _sample = [c['title'] for c in r.cards()[:10]]
        if len(_sample) < 5:
            kw_filter = _ambig
            execution.log(task, '  采样只有 %d 个标题 → %s'
                          % (len(_sample), '开过滤' if kw_filter else '关过滤'))
        else:
            _hit = sum(1 for t in _sample if keyword_hit(t, kw))
            _ratio = _hit / len(_sample)
            kw_filter = _ambig or (_ratio < 0.6)
            execution.log(task, '  采样 %d 个，%d 个含关键词（%.0f%%）→ %s%s'
                          % (len(_sample), _hit, _ratio * 100,
                             '开过滤' if kw_filter else '关过滤',
                             '（歧义词强制）' if _ambig else ''))

        seen_titles, up_count, done_videos = set(), {}, 0
        tap_fail = 0   # 连续点不进详情页计数，防空转
        # 2026-11-06 主人定：**不设上限**，一直往下翻，只有两个停点：
        #   ① 采集数量够了；② 真的翻到底（连续多次滑动无新卡片）。
        # 原来这里是 `while done_videos < videos_per_kw`（有目标时 500），
        # 且翻不动时"重搜同一关键词 5 次"——同词同排序结果不变，
        # 重搜纯空转（和小红书/抖音同一个毛病）。
        # 另外这里原本有个隐患：卡片都见过时 pool 为空，滑一下若仍有卡片就
        # `continue`，会**无限滑动永不结束**。现在用连续空滑计数兜住。
        empty_swipes = 0
        while True:
            r.check()
            if target_comments and total_leads >= target_comments:
                execution.log(task, '已入库 %d 条线索，达到目标 %d，停止' % (total_leads, target_comments))
                break
            pool = [c for c in r.cards() if c['title'] not in seen_titles]
            if not pool:
                empty_swipes += 1
                if empty_swipes > 6:
                    execution.log(task, '关键词「%s」已翻到底（连续 %d 次滑动无新视频，已入库 %d/%d 条线索）'
                                  % (kw, empty_swipes - 1, total_leads, target_comments or 0))
                    break
                _adb(serial, 'input swipe 540 1500 540 500 400')
                r.pause(2.5)
                continue
            empty_swipes = 0
            card = pool[0]
            seen_titles.add(card['title'])
            execution.log(task, '  视频：%s' % card['title'][:30])

            # 优先按元素 ref 点封面；ref 不可用时才退回裸坐标。
            clicked = False
            ref = (card.get('el') or {}).get('ref')
            if ref:
                try:
                    call('mobile_click_on_screen_at_coordinates', {'ref': ref}, serial)
                    clicked = True
                except Exception:
                    clicked = False
                if not clicked:
                    # 优先点封面元素中心（自适应任意宽度）；取不到才按屏宽比例兜底
                    c0 = center(card.get('el') or {})
                    if c0:
                        _tap(serial, c0[0], c0[1])
                    else:
                        _tap(serial, int(_profile(serial)['width'] * 700 / 1080), card['y'] + 15)
            r.pause(6)
            act = r.activity()
            if 'UnitedBizDetails' not in act:
                tap_fail += 1
                if tap_fail >= 5:
                    execution.log(task, '  连续 5 次点不进详情页（疑似坐标系统性偏移），停止避免空转')
                    break
                execution.log(task, '  没进详情页（当前 %s），跳过' % (act or '未知'))
                if not r.ensure_results(kw):
                    execution.log(task, '  界面回不到搜索结果页，结束该关键词')
                    break
                continue

            tap_fail = 0
            r.close_ad()
            head = r.header()
            ids = r.ids()
            aid = ids['aid']
            up_name = head['up'] or card['up']
            # 标题优先用搜索卡片：详情页有多个 id=title，header() 会抓到简介里的小标题
            title = card['title'] or head['title']
            up_uid = ''

            # 相关性校验：命中排除词或与主题无关的，整体跳过
            if kw_filter and not relevant_title(title, kw):
                execution.log(task, '    与关键词无关，跳过：%s' % title[:24])
                if not r.ensure_results(kw):
                    execution.log(task, '    界面回不到搜索结果页，结束该关键词')
                    break
                continue

            if not up_name:
                if not r.ensure_results(kw):
                    execution.log(task, '    界面回不到搜索结果页，结束该关键词')
                    break
                continue
            if up_count.get(up_name, 0) >= 3:
                execution.log(task, '    该 UP 已采 3 条，跳过')
                if not r.ensure_results(kw):
                    execution.log(task, '    界面回不到搜索结果页，结束该关键词')
                    break
                continue

            # UP 的 UID（点 UP 头像进主页）
            avatar = find_text(r.tree(), 'UP主头像')
            if avatar:
                r.tap_el(avatar, 'UP头像')
                r.pause(5)
                if 'LocalAuthorSpace' in r.activity():
                    up_uid = r.ids()['mid']
                _back(serial)
                r.pause(2.2)

            if not r.open_comments():
                execution.log(task, '    没能进入评论区，跳过')
                if not r.ensure_results(kw):
                    execution.log(task, '    界面回不到搜索结果页，结束该关键词')
                    break
                continue

            batch = []
            flushed = 0     # 这个视频里已经入过库的条数（边采边存用）
            rounds = 3 if not target_comments else 40
            for _round in range(rounds):
                r.check()
                # 目标按"入库线索数"算：batch 还没落库，用 total_leads+len(batch) 判断
                if target_comments and (total_leads + len(batch)) >= target_comments:
                    break
                if len(batch) >= comments_per_video or up_count.get(up_name, 0) >= 3:
                    break
                for c in r.comments(limit=6):
                    total_comments += 1
                    if _max_age:
                        try:
                            _ok, _ = _within_days(c.get('time', ''), _max_age, _base_ts)
                        except Exception:
                            _ok = True
                        if not _ok:
                            continue
                    if target_comments and (total_leads + len(batch)) >= target_comments:
                        break
                    if len(batch) >= comments_per_video or up_count.get(up_name, 0) >= 3:
                        break
                    cuid = r.commenter_uid(c['author'])
                    if cuid and up_uid and cuid == up_uid:
                        continue       # 评论人就是 UP 本人，没有联系价值
                    if not cuid:
                        continue       # 拿不到 UID 就无法私信，不入库
                    batch.append({
                        'comment_author': c['author'],
                        'comment_text': c['content'],
                        'time_text': c['time'],
                        'video_title': title,
                        'video_author': up_name,
                        'aid': aid,
                        'up_uid': up_uid,
                        'commenter_uid': cuid,
                        'bvid': '',
                        'keyword': kw,
                    })
                    up_count[up_name] = up_count.get(up_name, 0) + 1
                    # 边采边存：凑够 5 条立刻入库，不等整个视频跑完（2026-11-03）
                    if len(batch) - flushed >= 5:
                        chunk = batch[flushed:]
                        try:
                            lead_store.add_leads('bilibili', chunk, serial=serial,
                                                 dm_text=dm_tpl.replace('{keyword}', kw))
                            total_leads += len(chunk)
                            execution.log(task, '    入库 %d 条（累计 %d）' % (len(chunk), total_leads))
                        except Exception as exc:
                            execution.log(task, '    写入中转站失败：%s' % type(exc).__name__)
                        flushed = len(batch)
                if len(batch) >= comments_per_video or up_count.get(up_name, 0) >= 3:
                    break
                _adb(serial, 'input swipe 540 1600 540 900 400')
                r.pause(2.5)

            rest = batch[flushed:]
            if rest:
                try:
                    lead_store.add_leads('bilibili', rest, serial=serial,
                                         dm_text=dm_tpl.replace('{keyword}', kw))
                    total_leads += len(rest)
                    execution.log(task, '    入库 %d 条（累计 %d）' % (len(rest), total_leads))
                except Exception as exc:
                    execution.log(task, '    写入中转站失败：%s' % type(exc).__name__)

            total_videos += 1
            done_videos += 1
            if not r.ensure_results(kw):
                execution.log(task, '界面回不到搜索结果页，结束该关键词')
                break

    st = lead_store.stats('bilibili')['platforms']['bilibili']
    execution.log(task, '采集结束：本轮 %d 条 / %d 个视频；中转站共 %d 条（待处理 %d）'
                  % (total_leads, total_videos, st['total'], st['pending']))
    # 结束原因写进任务，界面详情直接展示（2026-11-03 主人要求"状态透明"）
    if target_comments:
        if total_leads >= target_comments:
            task['end_reason'] = '已达到入库目标：%d/%d 条线索（共读 %d 条评论）' % (total_leads, target_comments, total_comments)
        else:
            # 2026-11-06：补上命中率，便于判断是不是意向词太窄（和小红书/抖音一致）
            _rate = (100.0 * total_leads / total_comments) if total_comments else 0.0
            task['end_reason'] = (
                '目标未达成：已入库 %d/%d 条线索（共读 %d 条评论，命中率 %.1f%%）；'
                '原因：关键词下的视频已翻到底（连续多次滑动无新视频）'
                % (total_leads, target_comments, total_comments, _rate))
    else:
        task['end_reason'] = '未设入库目标，按配置上限跑完：%d 条线索（读 %d 条评论）' % (total_leads, total_comments)
    execution.log(task, '结束原因：' + task['end_reason'])
    try:
        from . import task_verification
        task_verification.mark(task, 'needs_review', '采集完成，请核对中转站线索质量')
    except Exception:
        pass
    # 兜底：run() 结束必须离开 queued/executing，否则前端永远显示"排队中"，
    # 用户以为任务还没开始（2026-11-02 实测 B 站截流卡死在这里）。
    if task.get('status') in (None, 'queued', 'executing'):
        task['status'] = 'completed'
    return True

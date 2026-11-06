"""TikTok 截流采集：按关键词搜索 → 进视频 → 读评论 → 评论人主页取 @handle → 入中转站。

设计前提（2026-11-01 手机7 实测）：
  · TikTok 控件 id 是混淆短名（h_d / al6 / cim / kko），同一次 dump 内可能重复，
    所以定位一律是「id + 语义 + 容器归属」三重条件，不缓存 ref、不缓存坐标。
  · 只读采集，不发送、不点赞、不关注。发送类控件（发布评论 bcr、纸飞机）绝不触碰。
  · 页面判定按「最上层特征」优先：评论区是叠层，底层 feed 的元素仍在树里。

唯一身份：TikTok 的 @handle（主页 kko）等价于抖音号，是跨机定位锚点。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .douyin_skill import SkillError, elements, foreground_package
from .mobile_gateway import MobileCallError, call

SKILL_ID = 'builtin:tiktok-lead'
SKILL = {
    'id': SKILL_ID,
    'name': 'TikTok 截流（按关键词找客户）',
    'description': '搜索关键词、进视频、读评论区，筛出线索并进主页取 @handle；不发送任何内容。',
}
PACKAGE = 'com.zhiliaoapp.musically'
PLATFORM = 'tiktok'
RESULTS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'lead_results'

#: 默认排除词：与抖音/小红书保持一致的最小集合，避免明显非客户被采集。
RISKY_DEFAULT_EXCLUDE = ('招聘', '招人', '兼职', '免费送', '投诉')


# ---------------------------------------------------------------- 基础工具
def _ref_ok(el) -> bool:
    """只有形如 @e12 的引用才能点，避免把脏数据当坐标。"""
    return bool(re.fullmatch(r'@e[1-9]\d*', str((el or {}).get('ref', ''))))


def _box(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    return {'x': c['x'], 'y': c['y'], 'width': c['width'], 'height': c['height']}


def _text_of(el, *keys):
    for key in keys:
        value = (el or {}).get(key)
        if value:
            return str(value)
    return ''


def _ident(el) -> str:
    """把 com.zhiliaoapp.musically:id/h_d 归一成 h_d。"""
    return str((el or {}).get('identifier') or '').rsplit(':id/', 1)[-1]


def by_id(tree, name):
    return [el for el in tree if _ident(el) == name]


def _inside(el, container) -> bool:
    box, outer = _box(el), _box(container)
    if not box or not outer:
        return False
    return (outer['x'] <= box['x'] and outer['y'] <= box['y']
            and box['x'] + box['width'] <= outer['x'] + outer['width']
            and box['y'] + box['height'] <= outer['y'] + outer['height'])


def _type(el) -> str:
    return str((el or {}).get('type') or '')


def screen_dimensions(result):
    for item in (result or {}).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        match = re.search(r'(\d{3,4})\s*[x×]\s*(\d{3,4})', text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return 1080, 1920


# ---------------------------------------------------------------- 页面识别
def page_kind(tree) -> str:
    """按最上层特征判页面。叠层优先：评论 > 主页 > 搜索结果 > 搜索 > 视频页。"""
    if not tree:
        return 'unknown'
    labels = [_text_of(e, 'label') for e in tree]
    texts = [_text_of(e, 'text') for e in tree]
    ids = {_ident(e) for e in tree}

    # 评论区：底部面板，关闭按钮 + 「N 条评论」标题
    if any(l == '关闭评论' for l in labels) or any(t.endswith('条评论') for t in texts):
        return 'comments'
    # 他人主页：有 @handle 按钮，且不是私信会话页。
    # ★不能要求「有消息按钮」：TikTok 对未互关/限制私信的用户根本不显示「消息」，
    # 那样会把大量正常主页判成 unknown（实测 2026-11-02 @s.afkr 主页就是这种）。
    handles = [t for t in texts if t.startswith('@') and len(t) > 1]
    if 'kko' in ids and handles and 'g8f' not in ids and 'iyi' not in ids:
        return 'profile'
    # 搜索结果：分栏条 + 搜索框
    if 'iyi' in ids and 'e22' in ids:
        return 'search_result'
    # 搜索输入页：只有搜索框，没有分栏
    if 'e22' in ids and 'plh' in ids:
        return 'search'
    # 视频页：右侧操作栏的点赞按钮（label 带「点赞视频」）
    if any(l.startswith('点赞视频') for l in labels):
        return 'video'
    return 'unknown'


# ---------------------------------------------------------------- 评论解析
def parse_comment_container(el):
    """把一条评论的容器解成 {author, content, avatar, like_text, time}。

    只返回能在本容器内找到昵称与正文的条目；缺一不算，避免把广告位当成评论。
    """
    return el


def read_comments(tree):
    """读取当前屏所有评论行。

    TikTok 的评论行容器是 h_d（FrameLayout），内部 chb 分组：
      al6 头像 / title 昵称 / cim 正文 / hfh 点赞 / pbc 赞数 / c_x 时间

    ★两个实测坑（2026-11-01）：
      1. 容器坐标会与上方「视频描述区」重叠，描述区的作者名也是 id=title，
         所以不能只按「容器内 title 恰一个」判断（会误收 Bannerverse 这种作者名）。
         规则：昵称必须满足 x 在头像右侧（container.x+100 起）且 y 在正文之上，取最靠下的一个。
      2. 部分评论只有头像+正文、没有昵称节点（首条常见）。这种无法核验评论人，跳过。
    """
    rows = []
    for container in by_id(tree, 'h_d'):
        cbox = _box(container)
        if not cbox:
            continue
        inner = [el for el in tree if _inside(el, container)]
        contents = [el for el in inner if _ident(el) == 'cim' and _box(el)]
        if not contents:
            continue
        # 正文取最靠上的一个（同一容器内多段正文时，第一条是主评论）
        contents.sort(key=lambda e: _box(e)['y'])
        content_el = contents[0]
        cy = _box(content_el)['y']
        # 昵称：必须与正文「同一列」（x 接近）且紧贴正文上方。
        # ★实测 2026-11-01：视频描述区的作者名也是 id=title，x=32，而评论昵称与正文
        # 同在 x≈145；只按「容器内」判断会把作者名污染成评论人昵称（5/5 全错）。
        cx = _box(content_el)['x']
        name_cands = [el for el in inner
                      if _ident(el) == 'title' and _box(el)
                      and abs(_box(el)['x'] - cx) <= 30
                      and 0 <= cy - _box(el)['y'] <= 100
                      and _text_of(el, 'text').strip()]
        if not name_cands:
            continue
        name_cands.sort(key=lambda e: _box(e)['y'])
        author = _text_of(name_cands[-1], 'text').strip()
        content = _text_of(content_el, 'text').strip()
        if not author or not content:
            continue
        avatars = [el for el in inner if _ident(el) == 'al6' and _ref_ok(el)]
        likes = [el for el in inner if _ident(el) == 'pbc']
        times = [el for el in inner if _ident(el) == 'c_x']
        rows.append({
            'author': author,
            'content': content,
            'avatar': avatars[0] if len(avatars) == 1 else None,
            'name_el': name_cands[-1],
            'container': container,
            'like_text': _text_of(likes[0], 'text').strip() if likes else '',
            'time': _text_of(times[0], 'text').strip() if times else '',
        })
    return rows


# ---------------------------------------------------------------- 视频/主页
def video_cards(tree):
    """搜索结果里的视频卡片。

    实测 2026-11-01（视频分栏）：卡片容器是 m12（FrameLayout），
    每张卡 509x1023（首屏两列）或 509x339（下方行），ref 可用可直接点。
    上一屏残留的 y<380 元素（状态栏/分栏条）不算卡片。
    """
    cards = []
    for el in by_id(tree, 'm12'):
        box = _box(el)
        if not box or not _ref_ok(el):
            continue
        if box['y'] < 380 or box['width'] < 300:
            continue
        cards.append(el)
    cards.sort(key=lambda e: (_box(e)['y'], _box(e)['x']))
    return cards


def describe_video(tree):
    """当前视频页的描述、作者，以及是不是广告。

    实测 2026-11-01：赞助视频（页面上有「赞助」标签）常常整体关闭评论
    （「广告主已关闭评论」），在它身上找评论入口纯属浪费往返。
    """
    desc = ''
    for el in by_id(tree, 'desc'):
        text = _text_of(el, 'text').strip()
        if text:
            desc = text
            break
    author = ''
    for el in by_id(tree, 'title'):
        # 视频页作者昵称是 Button id=title；评论行里的 title 已被上层限定，这里取最短的
        text = _text_of(el, 'text').strip()
        if text and not text.startswith('@') and len(text) < 60:
            author = text
            break
    sponsored_texts = {_text_of(e, 'text').strip() for e in tree}
    is_ad = bool(sponsored_texts & {'赞助', '廣告', '广告', 'Sponsored', 'Ad'})
    return {'desc': desc, 'author': author, 'is_ad': is_ad}


def profile_handle(tree):
    """他人主页的 @handle（唯一身份）与昵称。

    只认 kko 且 text 以 @ 开头；昵称取 it3，但必须与 handle 同屏出现。
    """
    handle, nickname = '', ''
    for el in by_id(tree, 'kko'):
        text = _text_of(el, 'text').strip()
        if text.startswith('@') and len(text) > 1:
            handle = text
            break
    if not handle:
        return '', ''
    for el in by_id(tree, 'it3'):
        text = _text_of(el, 'text').strip()
        if text and not text.startswith('@'):
            nickname = text
            break
    return handle, nickname


# ---------------------------------------------------------------- 取号
def looks_like_comment_input(tree) -> bool:
    """当前是不是评论「回复输入态」。

    实测 2026-11-01：点评论头像偶尔会落到评论行上，TikTok 直接弹出回复输入框
    （底部出现「发布评论」bcr 与「在评论中提及某人」gic）。这时绝不能再点任何东西，
    必须回退，否则有把内容发出去的风险。
    """
    labels = {_text_of(e, 'label') for e in tree}
    if '发布评论' in labels or '在评论中提及某人' in labels:
        return True
    return False


#: 单个视频级别的放弃信号：该视频采不下去了，但整个任务应当继续下一个视频。
class VideoSkipped(SkillError):
    pass


def collect_commenter_from_comment(page, comment, *, read_tree, tap, press, pause,
                                   check, attempts=12, dump=None, read_stable=None,
                                   tap_at=None):
    """唯一评论 → 容器内头像 → 主页读 @handle → BACK 一次 → 确认回到评论区。

    TikTok 的 @handle 可改，但它仍是当前唯一可核验的账号标识；
    昵称只做「直接点进去之后」的局部一致性参考，不作为身份。
    所有回调异常原样传播，不在异常清理里补 BACK。
    """
    check()
    author = str(comment.get('author') or '').strip()
    content = str(comment.get('content') or '').strip()
    if not author or not content or type(attempts) is not int or attempts < 1:
        raise SkillError('评论者取号：昵称/正文或读取次数无效，已停止')

    # 每次重新在当前树里定位同一条评论，不复用上一屏的 ref/坐标。
    # 优先用「稳定读」；并且要求目标头像的坐标在连续两次读取中一致才点——
    # 只比较「评论行数」不够：视频播放时列表会有小幅位移，落点会偏到视频缩略图上
    # （实测表现就是「误入视频页」）。
    target, avatar, last_box = None, None, None
    for attempt in range(3):
        current = (read_stable or read_tree)()
        found = None
        for row in read_comments(current):
            if row['author'] == author and row['content'] == content:
                found = row
                break
        if found is None or found.get('avatar') is None:
            if attempt == 2:
                break
            pause(0.6)
            continue
        box = _box(found['avatar'])
        if last_box is not None and box == last_box:
            target, avatar = found, found['avatar']
            break
        last_box = box
        target = found
        avatar = found['avatar']
        if attempt < 2:
            pause(0.6)
    if target is None:
        raise SkillError('评论者取号：当前屏已看不到该评论，已停止')
    if avatar is None:
        raise SkillError('评论者取号：评论容器内头像不存在或不唯一，已停止')

    check()
    # ★落点选择（实测 2026-11-02）：
    #   优先点**评论昵称**（title，x≈145，在正文上方），它远离评论里的视频/贴纸缩略图；
    #   点头像（x≈31）时缩略图紧贴其右侧，偶尔会误开视频页。昵称点不动再回退点头像。
    #   两者都不用元素正中心：坐标取偏左/偏上，进一步远离相邻可点区域。
    clicked = False
    name_el = target.get('name_el')
    nbox = _box(name_el) if name_el else None
    if tap_at is not None and nbox and nbox['width'] > 20:
        tap_at(nbox['x'] + max(8, nbox['width'] // 5),
               nbox['y'] + nbox['height'] // 2)
        clicked = True
    elif name_el is not None and _ref_ok(name_el):
        tap(name_el)
        clicked = True
    if not clicked:
        box = _box(avatar)
        if tap_at is not None and box and box['width'] > 40:
            tap_at(box['x'] + max(10, box['width'] // 4),
                   box['y'] + box['height'] // 2)
        else:
            tap(avatar)
    # ★点完先等等再判断：TikTok 的评论列表在视频播放时会轻微重排，
    # 立刻读树容易读到「正在过渡」的中间态，把成功误判成失败。
    pause(1.0)

    handle, nickname = '', ''
    last_tree = []
    for attempt in range(attempts):
        check()
        last_tree = read_tree()
        if looks_like_comment_input(last_tree):
            raise SkillError('评论者取号：误入评论回复输入态，已跳过')
        # 实测 2026-11-01：有时点击会落到评论里的视频缩略图上，直接进了单条视频页
        # （特征是出现「试用特效」+ 底部点赞视频）。这不是主页，立刻判失败回退。
        kind_now = page_kind(last_tree)
        if kind_now == 'video':
            raise SkillError('评论者取号：误入视频页，已跳过')
        handle, nickname = profile_handle(last_tree)
        if handle:
            break
        if attempt + 1 < attempts:
            pause(0.8)
    if not handle:
        if dump is not None:
            try:
                dump(last_tree, 'profile_fail')
            except Exception:                                  # noqa: BLE001
                pass
        raise SkillError('评论者取号：主页未读到 @handle，已停止')

    check()
    press('BACK')
    # ★实测 2026-11-01：从主页 BACK 回来经常落在**视频页**——TikTok 会把评论区关掉，
    # 而不是回到原来的评论列表。只要 handle 已经拿到、昵称也对得上，
    # 这一条就算成功；评论区由调用方重新打开（recover_to_comments 会处理）。
    for attempt in range(attempts):
        check()
        back = read_tree()
        kind_now = page_kind(back)
        if kind_now in ('comments', 'video'):
            if nickname and not same_person(author, nickname):
                raise SkillError(
                    '评论者取号：主页昵称「%s」与评论人「%s」不符，疑似点错，已跳过'
                    % (nickname, author))
            still_there = (kind_now == 'comments'
                           and any(r['author'] == author and r['content'] == content
                                   for r in read_comments(back)))
            return {
                'commenter_handle': handle,
                'commenter_nickname': nickname or author,
                'commenter_identity_source': 'direct_comment_profile_roundtrip',
                'commenter_identity_evidence': {
                    'comment_author': author,
                    'comment_text': content,
                    'profile_handle': handle,
                    'profile_nickname': nickname,
                    'returned_snapshot_equal': still_there,
                    'returned_page_kind': kind_now,
                },
            }
        if attempt + 1 < attempts:
            pause(0.8)
    raise SkillError('评论者取号：BACK后未回到评论页/视频页，已停止')


# ---------------------------------------------------------------- 判定
def _norm_name(text) -> str:
    """昵称归一：去空白/不可见字符/emoji/标点，只留字母数字与中文。"""
    t = str(text or '')
    t = re.sub(r'[\s\u200b-\u200f\u202a-\u202e\ufe0f]', '', t)
    t = re.sub(r'[^\w\u4e00-\u9fff]', '', t)
    return t.lower()


def same_person(comment_name, profile_name) -> bool:
    """评论人昵称与主页昵称是否指向同一人。

    ★实测 2026-11-01：评论列表重排时会点错行，进到**别人**的主页
    （案例：评论人 selia → 主页 Addison Rae）。这种错误数据比漏采更糟，
    因为后续会拿错人的 handle 去私信。无法判断时（归一化后为空）不做否决。
    """
    a, b = _norm_name(comment_name), _norm_name(profile_name)
    if not a or not b:
        return True
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
        return True
    return False


def is_placeholder_name(text) -> bool:
    """昵称是不是「无信息占位」（如 ... / ……）。

    实测 2026-11-01：个别用户昵称在评论列表里渲染成省略号，
    这类昵称入池后无法辨认是谁，应用主页读到的真实昵称替换。
    """
    stripped = str(text or '').strip()
    if not stripped:
        return True
    return all(ch in '.．…·、,， ' for ch in stripped)


def judge(comment, intent_words, exclude_words):
    """先排作者本人，再排排除词，最后看意向词；意向词为空=全量收录。"""
    text = comment.get('content', '')
    for word in exclude_words:
        if word and word in text:
            return False, '命中排除词「%s」' % word
    if not [w for w in intent_words if w]:
        return True, '未设意向词：全量收录'
    hits = [w for w in intent_words if w and w in text]
    if hits:
        return True, '命中意向词：' + '、'.join(hits[:3])
    return False, ''


def render_draft(template, comment, video):
    if not template.strip():
        return ''
    return (template.replace('{昵称}', comment.get('author', ''))
                    .replace('{评论}', comment.get('content', ''))
                    .replace('{作者}', video.get('author', ''))
                    .replace('{视频}', video.get('desc', '')))


# ---------------------------------------------------------------- 主流程
def run(task):
    # execution 依赖 fastapi（Web 服务）；脱离服务做真机链路测试时应当降级，
    # 否则一个 Web 依赖会把整条采集链拦住。
    try:
        from . import execution
    except Exception:                                          # noqa: BLE001
        execution = None

    cfg = task.get('lead') or {}
    import time as _t
    try:
        from .time_filter import within_days as _within_days
        _max_age = int(cfg.get('max_age_days') or 0)
        _base_ts = float(cfg.get('task_created_at') or task.get('created_at') or _t.time())
    except Exception:
        _max_age, _base_ts = 0, _t.time()
    keywords = [str(k).strip() for k in (cfg.get('keywords') or []) if str(k).strip()]
    if not keywords:
        raise SkillError('请至少填一个关键词')

    intent_words = [str(w).strip() for w in (cfg.get('intent_words') or []) if str(w).strip()]
    exclude_words = [str(w).strip() for w in (cfg.get('exclude_words') or []) if str(w).strip()]
    exclude_words += [w for w in RISKY_DEFAULT_EXCLUDE if w not in exclude_words]

    videos_per_keyword = int(cfg.get('videos_per_keyword') or 3)
    comments_per_video = int(cfg.get('comments_per_video') or 15)
    cooldown_every = max(1, int(cfg.get('cooldown_every') or 15))
    cooldown_seconds = max(1, int(cfg.get('cooldown_seconds') or 30))
    pace_factor = min(2.0, max(0.2, float(cfg.get('pace_factor') or 1.0)))
    dm_tpl = str(cfg.get('dm_template') or '')
    collect_commenter_id = bool(cfg.get('collect_commenter_id', True))
    #: 已知会卡住的视频（描述前缀）：上一轮放弃过的，本轮直接跳过，不重复浪费时间
    skip_video_descs = [str(x).strip() for x in (cfg.get('skip_video_descs') or [])
                        if str(x).strip()]

    #: 每采到 N 条就立刻入中转站（0 = 只在视频结束时写）
    flush_every = max(0, int(cfg.get('flush_every') or 5))

    #: 累计命中多少条线索就停（0 = 不限）。面板「采集评论总数」传的就是这个。
    target_comments = max(0, int(cfg.get('target_comments') or 0))

    serial = task['serial']
    stop = task['_stop']
    leads = task.setdefault('leads', [])
    stats = {'keywords': 0, 'videos': 0, 'comments': 0, 'leads': 0,
             'skipped': 0, 'pooled': 0, 'pool_dup': 0, 'no_handle': 0}
    task['lead_stats'] = stats

    def check():
        if stop.is_set():
            raise SkillError('任务已停止')

    def note(msg):
        if execution is not None:
            try:
                execution.log(task, msg)
                return
            except Exception:                                  # noqa: BLE001
                pass
        task.setdefault('events', []).append({'at': time.time(), 'message': msg})
        print(' *', msg, flush=True)

    def pause(seconds):
        if stop.wait(max(0.05, seconds * pace_factor)):
            check()

    def tree():
        check()
        last = None
        for attempt in range(3):
            check()
            try:
                return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))
            except (SkillError, MobileCallError) as exc:
                last = exc
                if attempt < 2:
                    pause(0.8)
        raise last

    def tap(el):
        check()
        if not _ref_ok(el):
            raise SkillError('控件引用不可用，已停止')
        call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)

    def tap_at(x, y):
        """按坐标点（用于需要精确避开相邻元素的地方，比如评论头像）。"""
        check()
        call('mobile_click_on_screen_at_coordinates', {'x': int(x), 'y': int(y)}, serial)

    def press(button):
        check()
        call('mobile_press_button', {'button': button}, serial)

    def swipe(direction='up', distance=700):
        check()
        call('mobile_swipe_on_screen', {'direction': direction, 'distance': distance}, serial)

    def foreground():
        return foreground_package(call('mobile_get_foreground_app', {}, serial))

    def settle(timeout=8.0):
        """等到页面能被识别；识别不了就停，不把未知页面当就绪。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            page = tree()
            if page_kind(page) != 'unknown':
                return page
            pause(0.6)
        return tree()

    def cd_tick(counter):
        if counter and counter % cooldown_every == 0:
            note('已处理 %d 次，按设置休息 %d 秒' % (counter, cooldown_seconds))
            pause(cooldown_seconds)

    def launch_app():
        note('打开 TikTok')
        if execution is not None:
            try:
                execution.execute(task, {'type': 'launch', 'package': PACKAGE})
            except Exception:                                  # noqa: BLE001
                call('mobile_launch_app', {'packageName': PACKAGE}, serial)
        else:
            call('mobile_launch_app', {'packageName': PACKAGE}, serial)
        for _ in range(12):
            pause(0.8)
            if foreground() == PACKAGE:
                return
        raise SkillError('TikTok 没有进入前台，已停止')

    def reset_home():
        """回到首页，保证从干净状态开始。TikTok 的叠层多，先退几层，再兜底重启。"""
        for _attempt in range(3):
            for _ in range(5):
                if foreground() != PACKAGE:
                    break
                page = tree()
                kind = page_kind(page)
                if kind == 'video':
                    home = [e for e in page
                            if _ident(e) == 'i5n' and _text_of(e, 'label') == '首页' and _ref_ok(e)]
                    if len(home) == 1 and 'selected' in json.dumps(page).lower():
                        return PAGE_READY
                if kind == 'comments':
                    press('BACK')
                    pause(1.0)
                    continue
                press('BACK')
                pause(0.9)
            press('HOME')
            pause(1.2)
            launch_app()
            pause(2.5)
        note('未确认回到首页，继续尝试')
        return None

    PAGE_READY = True

    def goto_search():
        """进入搜索输入页。

        审核教训（对齐小红书）：不能只看「有没有 EditText」，
        私信会话/评论输入框也是 EditText，会把关键词打进聊天草稿。
        这里必须有搜索框 e22 且没有分栏 iyi，才认作搜索输入页。
        """
        for _ in range(6):
            page = tree()
            kind = page_kind(page)
            if kind == 'search':
                return page
            if kind == 'search_result':
                press('BACK')
                pause(1.0)
                continue
            # 首页/已关注页右上角的搜索图标：id=fj6 且位于屏幕右侧
            # 说明：该图标无 label，用「同一 id 里 x 最大」做结构定位，不写死坐标。
            icons = [e for e in page if _ident(e) == 'fj6' and _ref_ok(e) and _box(e)]
            if icons:
                icons.sort(key=lambda e: _box(e)['x'])
                tap(icons[-1])
                pause(1.4)
                if page_kind(tree()) in ('search', 'search_result'):
                    continue
            press('BACK')
            pause(1.0)
        raise SkillError('未能进入 TikTok 搜索页，已停止')

    def search_keyword(keyword):
        page = goto_search()
        boxes = [e for e in page if _ident(e) == 'e22' and _ref_ok(e)]
        if len(boxes) != 1:
            raise SkillError('搜索框不存在或不唯一，已停止')
        tap(boxes[0])
        pause(1.2)                       # 等键盘就绪，避免吞掉首字符
        clears = [e for e in tree() if _ref_ok(e) and _text_of(e, 'label') == '清除搜索字段']
        if len(clears) == 1:
            tap(clears[0])
            pause(0.8)
            boxes = [e for e in tree() if _ident(e) == 'e22' and _ref_ok(e)]
            if len(boxes) == 1:
                tap(boxes[0])
                pause(0.8)
        call('mobile_type_keys', {'text': keyword}, serial)
        pause(0.8)
        page = tree()
        submits = [e for e in page if _ident(e) == 'plh' and _text_of(e, 'text') == '搜索'
                   and _ref_ok(e)]
        if len(submits) != 1:
            raise SkillError('搜索按钮不存在或不唯一，已停止')
        tap(submits[0])
        note('搜索「%s」' % keyword)
        pause(2.0)
        return settle()

    def to_video_tab(page, timeout=16.0):
        """切到「视频」分栏并等卡片真正出现。

        实测两坑（2026-11-01）：
          1. 分栏顺序由服务端决定，两次搜索顺序不同，必须按 label 文本找，不能用索引；
          2. 点分栏后内容是**异步加载**的，只等固定秒数会拿到空列表（表现为「没有取到视频卡片」）。
        """
        if page_kind(page) != 'search_result':
            return page
        switched = False
        swipes = 0
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            page = tree()
            if video_cards(page):
                return page
            if not switched:
                video_tabs = [e for e in page
                              if _text_of(e, 'label') == '视频' and _ref_ok(e) and _box(e)
                              and _box(e)['y'] < 450 and _box(e)['height'] < 200]
                if len(video_tabs) == 1:
                    tap(video_tabs[0])
                    switched = True
                    pause(1.6)
                    continue
                if swipes < 2:                 # 分栏可能在屏幕外，先横向滑两下
                    swipes += 1
                    swipe('left', 500)
                    pause(1.0)
                    continue
                return page
            pause(1.2)
        return page

    # 搜索结果里的视频卡片由模块级 video_cards() 提供（实测 m12 容器）
    def open_video(card, index):
        """点开第 index 个视频卡片，等到视频页；失败会重读卡片再试一次。

        实测 2026-11-02：搜索结果偶发「点了没反应」（卡片还在加载/被浮层挡住），
        单次失败就放弃整轮太浪费，这里给一次重试。
        """
        for attempt in range(2):
            check()
            if attempt > 0:
                # 重新读树拿到新鲜 ref，避免用已经失效的引用
                fresh = video_cards(tree())
                if index >= len(fresh):
                    break
                card = fresh[index]
            tap(card)
            pause(2.0)
            for _ in range(6):
                page = tree()
                if page_kind(page) == 'video':
                    return page
                pause(0.9)
        raise SkillError('第 %d 个视频没有打开成视频页，已停止' % (index + 1))

    def open_comments():
        page = settle()
        if page_kind(page) == 'comments':
            return page
        entries = [e for e in page
                   if _text_of(e, 'label').startswith('阅读或添加评论') and _ref_ok(e)]
        if len(entries) != 1:
            raise VideoSkipped('评论入口不存在或不唯一（%d 个），放弃该视频' % len(entries))
        tap(entries[0])
        for _ in range(6):
            pause(0.9)
            page = tree()
            if page_kind(page) == 'comments':
                return page
        raise VideoSkipped('评论区没有打开，放弃该视频')

    def close_comments():
        """关评论区。任何一步失败都不能把整轮任务带崩，最后用 BACK 兜底。"""
        try:
            page = tree()
            closers = [e for e in page if _text_of(e, 'label') == '关闭评论' and _ref_ok(e)]
            if len(closers) == 1:
                tap(closers[0])
            else:
                press('BACK')
        except Exception:                                     # noqa: BLE001
            try:
                press('BACK')
            except Exception:                                 # noqa: BLE001
                pass
        pause(1.0)
        for _ in range(4):
            try:
                if page_kind(tree()) == 'video':
                    return
            except Exception:                                 # noqa: BLE001
                return
            pause(0.8)

    def to_pool_rows(items):
        return [{'comment_author': row.get('author') or '',
                 'comment_text': row.get('content') or '',
                 'commenter_handle': row.get('commenter_handle') or '',
                 'commenter_nickname': row.get('commenter_nickname') or '',
                 'commenter_identity_source': row.get('commenter_identity_source') or '',
                 'commenter_identity_evidence': row.get('commenter_identity_evidence') or {},
                 'time_text': row.get('time') or '',
                 'video_desc': row.get('video_desc') or '',
                 'video_author': row.get('video_author') or '',
                 'author_handle': row.get('author_handle') or '',
                 'keyword': row.get('keyword') or '',
                 'dm_text': row.get('dm_draft') or ''} for row in items]

    def flush_pool(items):
        if not items:
            return
        try:
            from . import lead_store
            rows = []
            seen_handles = set()
            for row in to_pool_rows(items):
                handle = (row.get('commenter_handle') or '').strip()
                if not handle:
                    stats['no_handle'] += 1
                    continue
                # 同一批里同一 handle 只留第一条：同一个人发了两条评论，
                # 后续私信只应联系一次，重复入池会变成两条待办。
                if handle in seen_handles:
                    stats['pool_dup'] += 1
                    continue
                seen_handles.add(handle)
                if lead_store.exists_field('tiktok', 'commenter_handle', handle):
                    stats['pool_dup'] += 1
                    continue
                rows.append(row)
            if not rows:
                return
            pooled = lead_store.add_leads('tiktok', rows, serial=serial)
            stats['pooled'] += pooled['added']
            stats['pool_dup'] += pooled.get('duplicated', 0)
            if pooled['added']:
                note('已入中转站 %d 条' % pooled['added'])
        except Exception as exc:                                   # noqa: BLE001
            note('写入中转站失败（稍后仍会补写）：%s' % exc)

    def debug_dump(page, tag):
        """TIKTOK_DEBUG_DUMP=1 时把当前树落盘，便于离线分析定位问题。"""
        import os
        # 只有明确开启才落盘：'0'/'false' 都应当视为关闭（踩过一次：'0' 是真值字符串）
        if str(os.environ.get('TIKTOK_DEBUG_DUMP', '')).strip().lower() not in ('1', 'true', 'yes', 'on'):
            return
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            path = RESULTS_DIR / ('_dbg_%s_%s.json' % (tag, task['id']))
            rows = [{'id': _ident(e), 'type': _type(e),
                     'text': _text_of(e, 'text')[:80],
                     'label': _text_of(e, 'label')[:80],
                     'ref': _text_of(e, 'ref'), **(_box(e) or {})} for e in page]
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
            note('调试树已落盘：%s（%d 节点）' % (path.name, len(rows)))
        except Exception as exc:                               # noqa: BLE001
            note('调试落盘失败：%s' % exc)

    def comments_unavailable(page):
        """评论区不可采：广告主关闭评论、空评论、或需要登录。

        实测 2026-11-01：赞助视频（label 显示「赞助」）常见「广告主已关闭评论」，
        这类视频没有任何可采评论，直接跳过换下一个，不要空转两轮。
        """
        texts = {_text_of(e, 'text') for e in page}
        labels = {_text_of(e, 'label') for e in page}
        if '暂无评论' in texts or '广告主已关闭评论' in texts:
            return True
        if any('关闭评论' == l for l in labels) and '暂无评论' in texts:
            return True
        if any('登录后' in t or '登入後' in t for t in texts):
            return True
        return False

    def stable_tree(rounds=3, gap=0.7):
        """连续两次读到的评论行数一致，才认为列表稳定。

        实测 2026-11-01：视频播放时评论列表会轻微重排，拿「刚滚完/正在重排」
        的树去点，坐标会落到相邻的评论行上，表现就是「点头像却弹出回复输入框」。
        """
        prev = None
        last = []
        for _ in range(rounds):
            last = tree()
            key = len(by_id(last, 'h_d'))
            if prev is not None and key == prev:
                return last
            prev = key
            pause(gap)
        return last

    def recover_to_comments(max_back=4):
        """确保回到评论区；在视频页时重新打开评论区。

        ★实测教训：从主页 BACK 回来通常是视频页（评论区已被关掉），
        这时只按 BACK 会一路退回搜索结果，永远回不到评论区。
        评论入口偶尔第一次点不开（内容还在加载），所以要重试两轮再放弃。
        """
        for _ in range(max_back):
            check()
            kind = page_kind(tree())
            if kind == 'comments':
                return True
            if kind == 'video':
                for _ in range(2):
                    try:
                        open_comments()
                        if page_kind(tree()) == 'comments':
                            return True
                    except SkillError:
                        pass
                    pause(1.2)
            press('BACK')
            pause(1.0)
        return page_kind(tree()) == 'comments'

    def collect_comments(limit, video):
        rows, seen, empty_rounds = [], set(), 0
        duplicates = 0
        buffered = []          # 攒够 flush_every 条就落库
        while len(rows) < limit:
            check()
            page = tree()
            debug_dump(page, 'comments')
            if comments_unavailable(page):
                note('该视频评论区不可采（广告主关闭/暂无评论），跳过')
                return []
            fresh = 0
            for row in read_comments(page):
                key = (row['author'], row['content'])
                if key in seen:
                    continue
                fresh += 1
                if _max_age:
                    try:
                        _ok, _ = _within_days(row.get('time', ''), _max_age, _base_ts)
                    except Exception:
                        _ok = True
                    if not _ok:
                        seen.add(key)
                        stats['skipped'] += 1
                        continue
                ok, why = judge(row, intent_words, exclude_words)
                if not ok:
                    seen.add(key)
                    if why:
                        stats['skipped'] += 1
                    continue
                if collect_commenter_id:
                    # 进主页前先查重，省一次往返
                    try:
                        from . import lead_store
                        if lead_store.exists_field('tiktok', 'comment_author', row['author']) \
                                and lead_store.exists_field('tiktok', 'comment_text', row['content']):
                            seen.add(key)
                            duplicates += 1
                            continue
                    except Exception:
                        pass
                    try:
                        identity = None
                        # 取号最多试两次：第一次可能因为列表重排点到别的元素，
                        # 回退到评论区后用新的稳定树再试一次，仍有问题才跳过。
                        for take_attempt in range(2):
                            try:
                                identity = collect_commenter_from_comment(
                                    page, row, read_tree=tree, tap=tap, press=press,
                                    pause=pause, check=check, dump=debug_dump,
                                    read_stable=stable_tree, tap_at=tap_at)
                                break
                            except SkillError as exc:
                                message = str(exc)
                                recoverable = ('误入评论回复输入态' in message
                                               or '误入视频页' in message
                                               or '疑似点错' in message
                                               or 'BACK后未回到评论页' in message
                                               or '主页未读到 @handle' in message)
                                if not recoverable:
                                    raise
                                if not recover_to_comments():
                                    raise VideoSkipped('评论区退不回去，放弃该视频：%s' % message)
                                if take_attempt == 0:
                                    pause(1.2)
                                    page = stable_tree()
                                    continue
                                raise VideoSkipped(message)
                    except VideoSkipped:
                        # 已经判定要放弃这个视频了，直接上抛给主循环，不要被下面的通用处理再兜一次
                        raise
                    except SkillError as exc:
                        message = str(exc)
                        if '当前屏已看不到该评论' in message or '头像不存在' in message:
                            seen.add(key)
                            stats['skipped'] += 1
                            continue
                        if ('误入评论回复输入态' in message
                                or '误入视频页' in message
                                or '疑似点错' in message
                                or 'BACK后未回到评论页' in message
                                or '主页未读到 @handle' in message):
                            # 单条取号失败不能拖垮整个任务：先确定回到评论区，再跳过这一条。
                            # 退不回去说明页面状态不明，此时必须停下，不能继续点。
                            if not recover_to_comments():
                                raise VideoSkipped('评论区退不回去，放弃该视频：%s' % message)
                            seen.add(key)
                            stats['skipped'] += 1
                            note('取号失败已跳过：%s' % message)
                            page = tree()
                            continue
                        raise
                    row.update(identity)
                    # 评论列表里昵称显示成「...」时，用主页读到的真实昵称兜底
                    if is_placeholder_name(row.get('author')) \
                            and identity.get('commenter_nickname'):
                        row['author'] = identity['commenter_nickname']
                    # 取号往返后页面元素全变了，必须重读一次再继续
                    page = tree()
                seen.add(key)
                # ★不能 row.update(video)：video 里也有 author 键，会把评论人昵称
                # 覆盖成视频作者（实测 2026-11-01 把 5 条线索的 comment_author 全写成作者名）。
                row['video_desc'] = video.get('desc') or ''
                row['video_author'] = video.get('author') or ''
                row['keyword'] = keyword_now
                row['dm_draft'] = render_draft(dm_tpl, row, video)
                rows.append(row)
                buffered.append(row)
                stats['leads'] += 1
                # ★每采满 5 条就立刻入中转站：采集随时可能被打断，
                #   攒到视频结束再写库会丢掉已经拿到的线索。
                if flush_every and len(buffered) >= flush_every:
                    flush_pool(buffered)
                    buffered = []
                if len(rows) >= limit:
                    break
            if fresh == 0:
                empty_rounds += 1
                if empty_rounds >= 2:
                    break
            else:
                empty_rounds = 0
            if len(rows) >= limit:
                break
            swipe('up', 700)
            pause(1.0)
        if duplicates:
            note('本次跳过已在池中的评论 %d 条' % duplicates)
        if buffered:
            flush_pool(buffered)     # 收尾：把不满一批的也落库
        return rows

    # ------------------------------------------------------------ 执行
    keyword_now = ''
    launch_app()
    reset_home()

    for keyword in keywords:
        check()
        # 累计命中达到目标就收工（面板「采集评论总数」用这个）
        if target_comments and len(leads) >= target_comments:
            note('已采到 %d 条线索，达到目标 %d，停止' % (len(leads), target_comments))
            break
        keyword_now = keyword
        stats['keywords'] += 1
        page = search_keyword(keyword)
        page = to_video_tab(page)
        cards = video_cards(page)
        if not cards:
            note('关键词「%s」没有取到视频卡片，跳过' % keyword)
            continue
        note('关键词「%s」拿到 %d 个视频卡片' % (keyword, len(cards)))
        taken = 0
        attempted = 0
        # 2026-11-06 主人定：**不设上限**，一直往下翻，只有两个停点：
        #   ① 采集数量够了；② 真的翻到底（连续多次滑动无新卡片）。
        # 原来这里是 `while taken < videos_per_keyword and attempted < len(cards)`，
        # 只消费**当前屏**那几张卡片（attempted 是索引，索引超过就 break），
        # 一根手指都没往下滑过 —— 主人："一直都是看当前屏幕的内容，无意义"。
        # 现在改成：本屏消费完就滚动一屏，continue 继续消费新的一屏；
        # 连续 MAX_EMPTY_SWIPES 次滚动都拿不到可消费的新卡片，才认定"翻到底"。
        seen_descs = set()          # 跨屏去重：靠进视频后读到的描述
        empty_swipes = 0
        MAX_EMPTY_SWIPES = 6
        try:
            from . import ui_probe_targets as _U
            _h = _U.screen_profile(serial)['height']
        except Exception:                                 # noqa: BLE001
            _h = 1920
        while True:
            check()
            if target_comments and len(leads) >= target_comments:
                note('已采到 %d 条线索，达到目标 %d，停止' % (len(leads), target_comments))
                break
            page = tree()
            cards_now = video_cards(page)
            if attempted >= len(cards_now):
                # 本屏的卡片消费完了 → 往下翻一屏，继续找新视频
                empty_swipes += 1
                if empty_swipes > MAX_EMPTY_SWIPES:
                    note('关键词「%s」已翻到底（连续 %d 次滑动无新视频，已采 %d 条线索/%d 条评论）'
                         % (keyword, empty_swipes - 1, len(leads), stats['comments']))
                    break
                call('mobile_swipe_on_screen',
                     {'direction': 'up', 'distance': int(_h * 0.72), 'x': 540,
                      'y': int(_h * 0.75)}, serial)
                pause(1.5)
                attempted = 0
                continue
            try:
                page = open_video(cards_now[attempted], attempted)
            except SkillError as exc:
                note('第 %d 个视频打不开，跳过：%s' % (attempted + 1, exc))
                attempted += 1
                press('BACK')
                pause(1.2)
                continue
            attempted += 1
            video = describe_video(page)
            # 跨屏去重：滚动后同一视频可能再次出现，靠描述识别
            _desc_key = (video.get('desc') or '').strip()[:60]
            if _desc_key and _desc_key in seen_descs:
                note('视频「%s」已采过，跳过' % _desc_key[:18])
                press('BACK')
                pause(1.2)
                continue
            if _desc_key:
                seen_descs.add(_desc_key)
            empty_swipes = 0        # 成功打开一个视频 = 这屏还有内容，重置空滑计数
            stats['videos'] += 1
            note('视频「%s」作者 %s%s' % (video['desc'][:18] or '(无描述)',
                                          video['author'] or '(未知)',
                                          '（广告）' if video.get('is_ad') else ''))
            if video.get('is_ad'):
                note('广告视频：评论区通常整体关闭，跳过')
                stats.setdefault('skipped_videos', []).append(video['desc'][:40])
                press('BACK')
                pause(1.2)
                continue
            if any(video['desc'].startswith(s) for s in skip_video_descs):
                note('该视频在已知问题名单里，跳过')
                press('BACK')
                pause(1.2)
                continue
            rows = []
            try:
                open_comments()
                rows = collect_comments(comments_per_video, video)
            except VideoSkipped as exc:
                # 单个视频采不下去：记录后换下一个视频，整轮任务继续。
                note('视频「%s」放弃：%s' % (video['desc'][:18], exc))
                stats.setdefault('skipped_videos', []).append(video['desc'][:40])
                rows = []
            except SkillError as exc:
                note('视频「%s」采集失败：%s' % (video['desc'][:18], exc))
                raise
            finally:
                close_comments()
                pause(1.0)
                press('BACK')          # 回搜索结果
                pause(1.2)
            if not rows:
                note('该视频没有可采线索，换下一个')
                continue
            stats['comments'] += len(rows)
            leads.extend(rows)
            # 入池已经在 collect_comments 里按每 flush_every 条做过了，这里不再重复写
            note('本视频采到 %d 条线索' % len(rows))
            taken += 1
            cd_tick(taken)
        # 回首页准备下一个关键词
        reset_home()

    stats['finished'] = True
    note('采集结束：关键词 %d、视频 %d、评论 %d、线索 %d、入池 %d'
         % (stats['keywords'], stats['videos'], stats['comments'], stats['leads'], stats['pooled']))
    try:
        save_result(task['id'], {'stats': stats, 'leads': leads})
    except Exception:
        pass


def save_result(task_id, payload):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target = RESULTS_DIR / (re.sub(r'[^A-Za-z0-9_-]', '', str(task_id)) + '.json')
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return target

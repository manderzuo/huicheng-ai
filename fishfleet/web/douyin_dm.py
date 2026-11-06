"""抖音私信会话：主页（已核验抖音号）→ 打开会话 → 填入草稿 → **停在发送键前**。

本模块的边界是刻意画死的：
  · 只做 `open_chat` 与 `draft` 两件事，**没有任何点击「发送」的代码路径**；
  · 所有写设备的动作都要求"当次树里的新鲜元素"，不缓存、不跨页复用坐标；
  · 判页/判控件全部走 douyin_templates 的实测锚点，读不到唯一目标就停，不猜。

为什么停在发送键前（这是设计，不是没写完）：
  · 会话页的「发送」(`jan`) **只在输入框有内容时才出现在 UI 树**，所以它的出现本身就是
    "草稿已就绪"的客观信号，可以当验收判据用；
  · 同一页面上还有 `quick_phrase`（早上好/比心/[赞]…），点一下**立即发送、不再经过发送键**，
    所以"填草稿"阶段的任何误点都可能等价于发送。安全做法是:填完就停,
    由人在屏幕上确认后再按发送；
  · 首次私信会遇到"未互相关注的陌生人"安全提示与配额限制（实测首条 1 条），
    自动连发会被风控命中，也不符合本项目"不擅自发送"的铁律。

发送那一步需要的三个前置条件（给未来的发送器，本模块**不实现**）：
  1. 目标账号已用 `50g` 逐字核验 == 库里 commenter_douyin_id（不是昵称）；
  2. 草稿文字与预期**逐字相同**，且输入框里没有叠加的旧内容；
  3. 人已在界面上明确放行（本项目用 `needs_confirmation` 作闸门，见 execution_policy）。
"""
from __future__ import annotations

import re

from . import douyin_templates as templates
from .douyin_lead import _box, _text_of, _visible, by_id, pick_unique
from .douyin_skill import SkillError

ACCOUNT_RE = re.compile(r'抖音号[:：]\s*([A-Za-z0-9_][A-Za-z0-9_.-]{0,63})')

#: 会话页的判页锚点：输入框 + 顶部昵称
CHAT_INPUT_ID = 'msg_et'
CHAT_NICK_ID = 'vws'
CHAT_SEND_ID = 'jan'

#: 一旦被点到就等于"发出去了"或"改变对外状态"的控件。填草稿阶段必须绕开。
FORBIDDEN_LABELS = ('发送', '早上好', '比心', '关注', '音视频通话')

#: 快捷短语里的表情类（点一下**立即发送**，不经过发送键）。
#: 2026-11-06：`quick_phrase` 模板已移除（会多命中），这里补文字特征兜底，
#: 否则安全闸会因为拿不到模板而失效。
QUICK_PHRASE_RE = re.compile(
    r'^\[(困|赞|捂脸|玫瑰|比心|爱心|微笑|流泪|大笑|微笑R|赞R|飞吻R)\]$')


def _visible_in(tree, screen_size):
    """只保留屏内、可见、未被标记隐藏的节点；UI 树会残留上一个页面的节点。"""
    return [el for el in tree if _visible(el, screen_size)]


def _one(hits, label):
    """不存在与不唯一必须分开报：前者是"没这个控件"，后者才是"歧义"，
    调用方靠这个区分 error 与 ambiguous。"""
    if not hits:
        raise SkillError('私信：%s不存在，已停止' % label)
    if len(hits) > 1:
        raise SkillError('私信：%s不唯一（%d 个），已停止' % (label, len(hits)))
    return hits[0]


def _label(el):
    return _text_of(el, 'label', 'text')


def is_chat_page(visible):
    """同时有输入框和顶部昵称才算会话页；只看"发送"键会误判（它只在有内容时出现）。"""
    return len(by_id(visible, CHAT_INPUT_ID)) == 1 and len(by_id(visible, CHAT_NICK_ID)) >= 1


def send_button(visible):
    """发送键存在性。**只读，不点击**：它的存在用来证明草稿已就绪。"""
    hits = [el for el in by_id(visible, CHAT_SEND_ID) if _label(el) == '发送']
    return hits[0] if len(hits) == 1 else None


def _guard_click(el):
    """点击前的最后一道闸：目标文字命中禁止清单就拒绝，宁可停也不误发。"""
    blob = ' '.join(filter(None, [_text_of(el, 'label'), _text_of(el, 'text')]))
    for word in FORBIDDEN_LABELS:
        if word in blob:
            raise SkillError('私信：拒绝点击可能直接发送/改变状态的控件「%s」，已停止' % blob.strip())
    # 2026-11-06 修：`quick_phrase` 已从模板移除（会多命中），
    # 这里原来是 `targets['quick_phrase']` 直接下标 → KeyError，
    # 每次点击前的安检都崩（2号 实测「出错：'quick_phrase'」，回复 0 成功 2 失败）。
    # 安全闸不能丢（快捷短语点一下就立即发送），改为：有模板用模板，没有就按文字特征兜底。
    target = templates.PAGES['chat'].targets.get('quick_phrase')
    if target is not None and templates.matches(el, target):
        raise SkillError('私信：拒绝点击快捷短语（点了会立即发送），已停止')
    if QUICK_PHRASE_RE.match(blob.strip()):
        raise SkillError('私信：拒绝点击快捷短语（点了会立即发送），已停止：%s'
                         % blob.strip()[:24])


def verify_profile_account(page, expected_account):
    """在主页上用 `50g` 逐字核验抖音号。昵称只作记录，绝不作为身份依据。"""
    if not str(expected_account or '').strip():
        raise SkillError('私信：缺少预期抖音号，禁止用昵称降级，已停止')
    node = _one(by_id(page, '50g'), '主页抖音号')
    raw = _text_of(node, 'text', 'label')
    match = ACCOUNT_RE.fullmatch(raw.strip())
    if not match:
        raise SkillError('私信：主页抖音号格式无法解析（%r），已停止' % raw)
    observed = match.group(1)
    if observed != expected_account:
        raise SkillError('私信：主页抖音号与预期不一致（%s != %s），已停止'
                         % (observed, expected_account))
    nick = pick_unique(page, 'profile', 'nickname')
    return {'expected': expected_account, 'observed': observed, 'raw': raw,
            'nickname': _text_of(nick, 'text') if nick is not None else '',
            'match': 'exact_account'}


def _dismiss_popup(visible, tap, pause):
    """关掉遮挡页面的弹窗（如"共获得 N 个赞""我知道了"）。

    实测：点「私信」时点偏到旁边的**获赞数**，会弹出「XXX 共获得 17 个赞」
    面板把页面挡住，后续全失败。这类弹窗关掉就能继续，不该整条放弃。
    """
    labels = ('我知道了', '知道了', '关闭', '取消', '暂不', '以后再说')
    for el in visible:
        text = _text_of(el, 'label', 'text')
        if text in labels:
            tap(el)
            pause(0.8)
            return text
    return ''


def _follow_marks(visible):
    """主页上「关注」类控件的状态：{(x, y): label}。

    用来识别"点私信却点到了关注"——主页上这两个按钮常常并排，
    元素边界又可能互相包含，按坐标点中心就会偏。
    """
    marks = {}
    for el in visible:
        text = _text_of(el, 'label', 'text')
        if '关注' not in text:
            continue
        box = _box(el)
        if box:
            marks[(box['x'], box['y'])] = text
    return marks


def _follow_changed(before, after_items):
    """点击后，是否出现"原来关注、现在已关注"的位置变化。"""
    after = _follow_marks(after_items)
    for pos, label in after.items():
        old = before.get(pos)
        if old and '已关注' not in old and '已关注' in label:
            return True
    return False


def open_chat(page, *, expected_account, read_tree, tap, press, pause, check,
              screen_size, attempts=6):
    """主页 → 点「私信」→ 核实真的进入了会话页 → 返回会话状态。**不发送。**

    拒绝条件（任一命中即停，不做任何补偿动作）：
      · 主页抖音号缺失/不唯一/与预期不符；
      · 主页没有唯一的「私信」按钮；
      · 点击后没有进入会话页（可能在任意页面，绝不当成会话页继续填字）。
    """
    check()
    if not str(expected_account or '').strip():
        raise SkillError('私信：缺少预期抖音号，已停止')

    visible = _visible_in(page, screen_size)
    profile = verify_profile_account(visible, expected_account)

    dm_target = templates.PAGES['profile'].targets['dm']
    candidates = [el for el in visible if templates.matches(el, dm_target)]
    if not candidates:
        raise SkillError('私信：主页「私信」按钮不存在，已停止')
    dm = _one(candidates, '主页「私信」按钮')
    if _box(dm) is None:
        raise SkillError('私信：「私信」按钮缺有效坐标，已停止')
    _guard_click(dm)

    # 点击「前」记录关注类控件状态。主页上「私信」和「关注」常常并排，
    # 偶尔会点偏到关注上——关注可以容忍（用户已确认），但系统必须**知道**
    # 自己点偏了，并且**不要就此放弃**：还在主页，重新找私信按钮再试。
    before_follow = _follow_marks(visible)
    check()
    tap(dm)
    misclicked = False

    for attempt in range(attempts):
        check()
        current = _visible_in(read_tree(), screen_size)
        if is_chat_page(current):
            info = chat_state(current)
            info.update({'profile_account': profile['observed'],
                         'profile_nickname': profile['nickname'],
                         'opened': True,
                         'follow_misclick': misclicked})
            return info
        # 点偏到「关注」上了：页面仍停在主页，没有进会话。
        # 不放弃——重新找一次私信按钮，避开关注按钮再点一下（只补一次）。
        if not misclicked and _follow_changed(before_follow, current):
            misclicked = True
            retry = [el for el in current if templates.matches(el, dm_target)]
            if retry:
                button = _one(retry, '主页「私信」按钮')
                if _box(button) is not None:
                    _guard_click(button)
                    tap(button)
                    pause(1.2)
                    continue
            break
        # 页面里连一个抖音节点都没有：多半是跳到了登录页/外部应用。
        # 实测未登录账号点「私信」会跳 DYLoginActivity，此时若只报
        # "未核实进入会话页"，排查者根本想不到是账号没登录。
        if current and not any('aweme' in str(el.get('identifier', '')) for el in current):
            raise SkillError(
                '私信：点击后已离开抖音界面（很可能跳转到登录页）。'
                '该设备可能未登录抖音账号——采集可以匿名进行，但私信必须登录，'
                '请先在这台手机上登录抖音后再提取')
        # 界面不是会话页、也不是主页（说明误入了播放页/评论区等）：
        # 先按用户建议用**系统返回键**退回去，再继续等，而不是直接报错。
        # 但「还在主页」时不要退——那时 BACK 会把主页也退掉，反而更糟。
        in_profile = bool(by_id(current, 'tk1')) or bool(by_id(current, '50g'))
        # 先尝试关掉遮挡弹窗（点偏到获赞数会弹「共获得 N 个赞」面板）
        if _dismiss_popup(current, tap, pause):
            continue
        if not in_profile and attempt + 1 < attempts:
            press('BACK')
            pause(1.0)
            continue
        if attempt + 1 < attempts:
            pause(0.8)
    raise SkillError('私信：点击「私信」后未核实进入会话页（当前不是已核验的会话页），已停止')


def chat_state(visible):
    """会话页可观测事实；不点击任何东西。"""
    box = _one(by_id(visible, CHAT_INPUT_ID), '会话输入框')
    nick = by_id(visible, CHAT_NICK_ID)
    send = send_button(visible)
    safety = [el for el in visible
              if '未互相关注的陌生人' in _text_of(el, 'label', 'text')]
    return {
        'input_box': _box(box),
        'input_existing_text': _text_of(box, 'text'),
        'chat_nickname': _text_of(nick[0], 'text', 'label') if nick else '',
        'send_present': send is not None,
        'safety_tip': bool(safety),
        'send_clicked': False,
    }


def draft(page, text, *, read_tree, tap, type_text, pause, check, screen_size,
          attempts=6, tap_at=None):
    """会话页 → 点输入框 → 输入文案 → 校验「文字逐字一致 + 发送键已出现」→ 停住。

    **不点发送。** 返回里 `drafted` 才是能否进入人工确认的判据；
    只有 `drafted=True` 才代表屏幕上的状态是"文案已填好，手按发送即可"。
    `type_text` 由调用方注入（真实实现走 gateway，测试用假函数）。
    """
    check()
    wanted = str(text or '').strip()
    if not wanted:
        raise SkillError('私信：文案为空，不进入会话')

    visible = _visible_in(page, screen_size)
    if not is_chat_page(visible):
        raise SkillError('私信：当前不是会话页，拒绝填字，已停止')

    box = _one(by_id(visible, CHAT_INPUT_ID), '会话输入框')
    if _text_of(box, 'text'):
        # 旧草稿 + 新文案会拼成一句怪话，宁可停也不污染
        raise SkillError('私信：输入框已有内容，拒绝叠加旧草稿，已停止')
    if _box(box) is None:
        raise SkillError('私信：输入框缺有效坐标，已停止')
    _guard_click(box)

    check()
    # 点输入框的**左侧文字区**，不点元素中心。
    # 实测踩坑：按 ref 点元素中心会落在输入框右侧的表情按钮上，
    # 抖音随即弹出「只有关注的用户才可以搜索表情」——一个纯误触。
    guard = _box(box)
    if tap_at is not None and guard:
        tap_at(int(guard['x'] + guard['width'] * 0.25),
               int(guard['y'] + guard['height'] / 2))
    else:
        tap(box)

    check()
    # 输入是设备写操作：一次写完，不重试（重放会重复插入，无法回滚）。
    type_text(wanted)
    check()
    pause(0.6)

    landed = None
    for attempt in range(attempts):
        check()
        current = _visible_in(read_tree(), screen_size)
        if not is_chat_page(current):
            raise SkillError('私信：输入后不再是已核验的会话页，已停止')
        field = _one(by_id(current, CHAT_INPUT_ID), '会话输入框')
        observed = _text_of(field, 'text')
        send = send_button(current)
        if observed == wanted and send is not None:
            landed = {'observed_text': observed, 'send_present': True,
                      'send_box': _box(send), 'send_clicked': False}
            break
        if attempt + 1 < attempts:
            pause(0.6)
    if landed is None:
        raise SkillError('私信：文案未逐字落到输入框或发送键未出现，已停止')

    return {
        'drafted': True,
        'input_text': landed['observed_text'],
        'send_present': True,
        'send_box': landed['send_box'],
        'send_clicked': False,
        'evidence': {
            'text_matches_expected': True,
            'send_button_present': True,
            'send_button_id': CHAT_SEND_ID,
            'note': '草稿已填好并核实，停在发送键前；本模块不含发送动作',
        },
    }

"""小红书截流模块自检：纯函数 + mock，不连设备、不启动服务、不碰账号。

覆盖 2026-11-01 审核后修复的四类问题：
  1. 昵称比对三态（空值不再冒充「一致」）
  2. 会话页发送键判定（有字但无发送键不算就绪）
  3. 草稿已有内容时拒绝写入、取消后停止分块输入
  4. B 端身份核验（读不到号 / 号不符 / 未进会话页都必须停止）

运行：python fishfleet/web/xiaohongshu_lead_selftest.py
"""
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import xiaohongshu_lead as L  # noqa: E402

failures = []


def check(name, cond, detail=''):
    if cond:
        print('OK   ' + name)
    else:
        failures.append(name)
        print('FAIL ' + name + ' ' + str(detail))


def edit(text, value=None):
    return {'ref': '@e1', 'type': 'android.widget.EditText', 'text': text,
            'value': (text if value is None else value),
            'coordinates': {'x': 158, 'y': 1609, 'width': 664, 'height': 105}}


def send_el():
    return {'ref': '@e85', 'type': 'android.widget.TextView', 'text': '发送',
            'coordinates': {'x': 928, 'y': 1619, 'width': 99, 'height': 84}}


def quota_el():
    return {'ref': '@e89', 'type': 'android.widget.TextView',
            'text': '对方关注或回复你之前，最多只能发送1条消息',
            'coordinates': {'x': 0, 'y': 679, 'width': 1080, 'height': 40}}


def payload(rows):
    return {'content': [{'type': 'text', 'text': json.dumps(rows, ensure_ascii=False)}]}


# ---------------- 1. 昵称三态 ----------------
check('昵称完全一致', L.nickname_status('兔子威武', '兔子威武') == 'match')
check('昵称带 emoji 视为一致', L.nickname_status('兔子威武🐯', '兔子威武') == 'match')
check('明显不同的人', L.nickname_status('Bob', 'Alice') == 'mismatch')
check('主页读不到昵称 → unknown', L.nickname_status('', 'Alice') == 'unknown')
check('评论昵称为空 → unknown', L.nickname_status('Alice', '') == 'unknown')
check('unknown 不算通过', L.nickname_ok('', 'Alice') is False)

# ---------------- 2. 会话页判定 ----------------
info = L.session_info([edit(''), quota_el()])
check('空输入没有发送键', info['send_present'] is False)
check('配额解析为 1 条', info['quota_limit'] == 1)
info2 = L.session_info([edit('hello'), send_el(), quota_el()])
check('有内容时发送键出现', info2['send_present'] is True)
check('发送键坐标正确', info2['send_box'] == {'x': 928, 'y': 1619, 'width': 99, 'height': 84})
check('优先读 value 字段', L.session_info([edit('abc', value='真实内容')])['input_value'] == '真实内容')

# ---------------- 3. 草稿写入保护 ----------------
def draft(screens, text, stop=None):
    it = iter(screens)
    calls = []
    def fake_call(tool, args, serial):
        if tool == 'mobile_list_elements_on_screen':
            return payload(next(it))
        calls.append(args)
        return {'content': []}
    with patch.object(L, 'open_chat', return_value={'nickname': 'demo'}), \
         patch.object(L, 'call', side_effect=fake_call), \
         patch.object(L.time, 'sleep', return_value=None):
        try:
            return 'ok', L.draft_message('mock', '123456', text, stop=stop), calls
        except Exception as exc:                       # noqa: BLE001
            return 'raise', str(exc), calls

kind, detail, _ = draft([[edit('旧草稿')]], '新文案')
check('已有草稿时拒绝写入', kind == 'raise' and '已有内容' in detail, detail)

kind, res, _ = draft([[edit('')], [edit('新文案')]], '新文案')
check('有字但无发送键 → 不算就绪', kind == 'ok' and res['drafted'] is False, res)
check('未就绪时给出原因', kind == 'ok' and '发送' in str(res.get('mismatch')), res)

kind, res, _ = draft([[edit('')], [edit('新文案'), send_el()]], '新文案')
check('有字且有发送键 → 就绪', kind == 'ok' and res['drafted'] is True, res)

stop = threading.Event()
written = []
def fake_call_cancel(tool, args, serial):
    if tool == 'mobile_list_elements_on_screen':
        return payload([edit('')])
    written.append(args.get('text'))
    stop.set()          # 第一块写完就取消
    return {'content': []}
with patch.object(L, 'open_chat', return_value={'nickname': 'demo'}), \
     patch.object(L, 'call', side_effect=fake_call_cancel), \
     patch.object(L.time, 'sleep', return_value=None):
    try:
        L.draft_message('mock', '123456', 'x' * 2500, stop=stop)
        check('取消后应抛停止', False, '未抛异常')
    except L.SkillError:
        check('取消后立即停止分块输入', len(written) == 1, written)

# ---------------- 4. B 端身份核验 ----------------
def open_chat_mock(profile_rows, activity):
    screens = iter([payload(profile_rows), payload([edit('')])])
    def fake_call(tool, args, serial):
        if tool == 'mobile_list_elements_on_screen':
            return next(screens)
        if tool == 'mobile_get_foreground_app':
            return {'content': [{'type': 'text',
                                 'text': 'Foreground app: 小红书 (com.xingin.xhs), activity: %s' % activity}]}
        return {'content': []}
    with patch.object(L, 'relocate_by_id', return_value={'xhs_id': '123456', 'y': 508}), \
         patch.object(L, 'call', side_effect=fake_call), \
         patch.object(L.time, 'sleep', return_value=None):
        try:
            return 'ok', L.open_chat('mock', '123456')
        except Exception as exc:                       # noqa: BLE001
            return 'raise', str(exc)


DM = [{'ref': '@e2', 'type': 'android.widget.TextView', 'text': '私信',
       'coordinates': {'x': 700, 'y': 800, 'width': 100, 'height': 60}}]
ID = [{'ref': '@e3', 'type': 'android.widget.TextView', 'text': '小红书号：123456',
       'coordinates': {'x': 57, 'y': 357, 'width': 240, 'height': 45}}]
WRONG = [{'ref': '@e3', 'type': 'android.widget.TextView', 'text': '小红书号：999',
          'coordinates': {'x': 57, 'y': 357, 'width': 240, 'height': 45}}]
CHAT_ACT = 'com.xingin.im.ui.activity.ChatActivity'
OTHER_ACT = 'com.xingin.matrix.v2.profile.newpage.NewOtherUserActivity'

kind, detail = open_chat_mock(DM, CHAT_ACT)
check('主页读不到号就停止', kind == 'raise' and '无法确认身份' in detail, detail)
kind, detail = open_chat_mock(DM + WRONG, CHAT_ACT)
check('主页号不符就停止', kind == 'raise' and '不符' in detail, detail)
kind, detail = open_chat_mock(DM + ID, OTHER_ACT)
check('未进入会话页就停止', kind == 'raise' and '会话页' in detail, detail)
kind, detail = open_chat_mock(DM + ID, CHAT_ACT)
check('正常路径可打开会话', kind == 'ok' and detail['opened'] is True, detail)

# ---------------- 5. 对方关闭私信权限 ----------------
def open_chat_no_dm(profile_rows):
    screens = iter([payload(profile_rows), payload([edit('')])])
    def fake_call(tool, args, serial):
        if tool == 'mobile_list_elements_on_screen':
            return next(screens)
        if tool == 'mobile_get_foreground_app':
            return {'content': [{'type': 'text',
                                 'text': 'Foreground app: 小红书 (com.xingin.xhs), activity: %s' % OTHER_ACT}]}
        return {'content': []}
    with patch.object(L, 'relocate_by_id', return_value={'xhs_id': '123456', 'y': 508}), \
         patch.object(L, 'call', side_effect=fake_call), \
         patch.object(L.time, 'sleep', return_value=None):
        return L.open_chat('mock', '123456')

detail = open_chat_no_dm(DM[:0] + ID)          # 有号但主页没有「私信」按钮
check('对方没开私信 → 返回结构化结果而非抛异常',
      isinstance(detail, dict) and detail.get('opened') is False
      and detail.get('reason') == 'no_dm_button', detail)

print()
if failures:
    print('失败 %d 项：%s' % (len(failures), '、'.join(failures)))
    sys.exit(1)
print('全部通过')

"""风险路由：只有"发出去"的动作需要二次确认，浏览内容一律直接执行。

判定原则（按动作本身判断，不按整条任务描述判断）：
  · 浏览/导航/滑动/输入/打开应用/读取页面 → 不确认
  · 会对外产生内容或改变账号对外状态的动作（发送/发布/评论提交/点赞/关注/收藏/分享/私信/付款/删除…） → 确认
  · 不可逆的设备破坏操作（卸载/清除数据/恢复出厂） → 无条件确认
关键点：不再用整条 goal 全文匹配来决定确认，否则一个"发布作品"任务里
前面几十步的浏览、输入、翻页都会被卡住。goal 只在拿不到控件信息时兜底。
"""
import re
import xml.etree.ElementTree as ET

# 真正"发出去"或不可逆的动词。刻意不包含"评论"这个名词：
# 点"评论""全部评论""评论区"只是进入浏览；只有"发表/发送/回复"才算发送。
SEND_ACTION = re.compile(
    r'发布|发表|发送|提交|回复|私信|留言|点赞|喜欢|关注|取关|取消关注|收藏'
    r'|分享|转发|举报|拉黑|删除|移除|清理'
    r'|付款|支付|购买|下单|转账|充值|提现'
    r'|登录|退出登录|注销|换绑|绑定|改密|实名|验证码|密码'
    r'|授权|允许|始终允许|权限|卸载|恢复出厂|格式化|清除数据|清除所有数据'
    r'|post|publish|send|submit|reply|comment out|share|like|follow|unfollow'
    r'|favorite|pay|buy|order|transfer|recharge|delete|remove|login|logout'
    r'|password|verify|auth|allow|permission|uninstall|factory|wipe|erase',
    re.I,
)

# 不可逆设备破坏：与账号规则无关，无条件确认。
DANGEROUS_DEVICE = re.compile(r'卸载|恢复出厂|格式化|恢复出厂设置|清除所有数据|uninstall|factory|wipe|erase', re.I)

# 纯读取类 MCP 工具，永远不会改变手机状态。
MCP_READONLY = frozenset({
    'mobile_take_screenshot', 'mobile_list_elements_on_screen', 'mobile_get_foreground_app',
    'mobile_get_screen_size', 'mobile_get_orientation', 'mobile_list_apps',
})
# 设备状态写入但不对平台"发送"任何内容，属于可逆本地操作。
MCP_LOCAL = frozenset({
    'mobile_launch_app', 'mobile_terminate_app', 'mobile_swipe_on_screen',
    'mobile_clipboard', 'mobile_set_location', 'mobile_press_button',
})


def open_app_intent(goal):
    """“打开X/启动X/进入X”：返回 (包名推测, 其余目标描述)；无法推测时 (None, goal)。"""
    m = re.match(r'^\s*(?:请)?(?:帮我)?(?:打开|启动|进入|打开手机的?)(.*?)[。！!，,\s]*$', goal or '')
    if not m:
        return (None, goal)
    name = (m.group(1) or '').strip().replace('手机的', '').strip()
    if not name or name in ('设置', '系统设置'):
        return (None, goal)
    return (name or None, goal)


def settings_target(goal):
    """仅识别打开设置页面的完整请求；带开关/连接/改配置等附加目标不命中。"""
    m = re.fullmatch(
        r'\s*(?:请)?(?:帮我)?(?:打开|进入)(?:手机的?|系统)?'
        r'(设置|系统设置|无线网络设置|无线局域网设置|无线设置|网络设置|WLAN设置|Wi[- ]?Fi设置)'
        r'(?:界面|页面)?[。！!\s]*',
        goal or '', re.I,
    )
    if not m:
        return None
    name = m.group(1)
    if re.fullmatch(r'无线网络设置|无线局域网设置|无线设置|WLAN设置|Wi[- ]?Fi设置', name, re.I):
        return 'wifi'
    if name == '网络设置':
        return 'connections'
    return 'settings'


def settings_intent(goal):
    return settings_target(goal) == 'settings'


NAV_KEYS = {
    'APP_SWITCH': re.compile(r'最近任务|最近使用|切换窗口|切换应用|后台应用|多任务|任务界面|recents?|app switch', re.I),
    'BACK': re.compile(r'^\s*(请|帮我)?(按|点)?返回(上一页|键|一次)?\s*[。！!]?\s*$'),
    'HOME': re.compile(r'回到桌面|返回桌面|回主页|回到主页|按主页键|home ?键', re.I),
}


def nav_intent(goal):
    """确定性的系统导航键意图：避免模型在底栏猜坐标。"""
    for key, pattern in NAV_KEYS.items():
        if pattern.search(goal or ''):
            return key
    return None


def _points(action):
    if action.get('type') == 'tap':
        return [(action['x'], action['y'])]
    if action.get('type') == 'swipe':
        return [(action['x1'], action['y1']), (action['x2'], action['y2'])]
    if action.get('type') == 'mobile_mcp' and action.get('tool') == 'mobile_click_on_screen_at_coordinates':
        args = action.get('arguments') or {}
        if 'x' in args and 'y' in args:
            return [(args['x'], args['y'])]
    return []


# MCP 元素列表是纯文本，一行一个控件：
#   @e14 TextView text="发布" label="..." id="..." at=845,153 size=116x115
_ELEMENT = re.compile(
    r'^(?P<ref>@\S+)\s+(?P<type>\S+)(?P<attrs>.*?)\bat=(?P<x>\d+),(?P<y>\d+)\s+size=(?P<w>\d+)x(?P<h>\d+)',
    re.M,
)


def hit_labels_from_elements(action, elements=''):
    """用 Mobile MCP 返回的控件列表判断落点控件。

    比 uiautomator 的 XML 树稳定得多（XML 常因无障碍服务冲突抓不到），
    而且这份列表本来就已经为模型准备好，不需要额外请求。
    """
    points = _points(action)
    if not points or not elements:
        return []
    labels = []
    for m in _ELEMENT.finditer(elements):
        x, y, w, h = (int(m.group(k)) for k in ('x', 'y', 'w', 'h'))
        if not all(x <= px < x + w and y <= py < y + h for px, py in points):
            continue
        labels.append(m.group('attrs'))
    return labels


def hit_labels(action, xml=''):
    """返回点击/滑动落点上的控件标签（text + content-desc + resource-id + class）。"""
    points = _points(action)
    if not points or not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError):
        return []
    labels = []
    for node in root.iter('node'):
        m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if not all(x1 <= x < x2 and y1 <= y < y2 for x, y in points):
            continue
        labels.append(' '.join([
            node.get('text', ''), node.get('content-desc', ''),
            node.get('resource-id', ''), node.get('class', ''),
        ]))
    return labels


# 否定/排除语境：这些词后面的动作是被明确要求"不要做"的，不能算发送意图。
_NEGATED = re.compile(
    r'(?:不要|不用|无需|不需要|别|禁止|切勿|勿|避免|不准|不许|不许要|看看|只看|仅看|浏览)[^，,。；;\n]{0,12}',
    re.I,
)


def send_intent(goal):
    """判断任务本身是不是"发送任务"：先去掉否定语境，再看是否含发送动词。"""
    text = _NEGATED.sub('', goal or '')
    return bool(SEND_ACTION.search(text))


def needs_confirmation(action, goal, xml='', elements=''):
    """只有发送类动作需要二次确认；浏览、导航、输入一律直接执行。"""
    kind = action.get('type')
    goal_text = goal or ''

    # 打开应用永远不需要确认（两个通道保持一致）。
    if kind == 'launch':
        return False

    # 只读工具永远不需要确认。
    if kind == 'mobile_mcp':
        tool = action.get('tool', '')
        if tool in MCP_READONLY:
            return False
        # 卸载/清数据等不可逆操作：无条件确认。
        if tool in ('mobile_uninstall_app', 'mobile_clear_app_data', 'mobile_install_app'):
            return True

    # 控件识别优先用 MCP 元素列表，其次才是 uiautomator 的 XML 树。
    labels = hit_labels_from_elements(action, elements) or hit_labels(action, xml)

    # 不可逆设备破坏：无论标签还是目标命中都确认。
    if any(DANGEROUS_DEVICE.search(label or '') for label in labels):
        return True
    if not labels and DANGEROUS_DEVICE.search(goal_text):
        return True

    # 滑动、输入文字本身不发送任何内容：输入完还要按发送才确认。
    if kind == 'swipe':
        return False
    if kind == 'text':
        return False

    # 回车/删除：只有在"发送语境"下才确认。
    if kind == 'key':
        return action.get('key') in ('enter', 'delete') and send_intent(goal)
    if kind == 'mobile_mcp' and action.get('tool') == 'mobile_press_button':
        button = (action.get('arguments') or {}).get('button', '')
        return button in ('ENTER', 'BACKSPACE') and send_intent(goal)

    # 本地设备操作（返回键/切应用/设剪贴板/定位/结束应用）：不对外发送。
    if kind == 'mobile_mcp' and action.get('tool') in MCP_LOCAL:
        return False

    # 点击类：看落点上的按钮是不是"发送动作"。
    if labels:
        return any(SEND_ACTION.search(label or '') for label in labels)

    # 认不出控件时，只在任务本身是发送任务时兜底确认；纯浏览不拦。
    return send_intent(goal)


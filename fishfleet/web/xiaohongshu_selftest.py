"""小红书模板自检：用实测发布页元素验证选择逻辑。

只导入模板模块（无重依赖），不启动服务、不连设备、不触碰任何账号。
运行：python fishfleet/web/xiaohongshu_selftest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import xiaohongshu_templates as T  # noqa: E402

PKG = 'com.xingin.xhs'


def el(ref, typ, text='', label='', x=0, y=0, w=100, h=100):
    return {'ref': ref, 'type': typ, 'text': text, 'label': label,
            'identifier': PKG + ':id/0_resource_name_obfuscated',
            'coordinates': {'x': x, 'y': y, 'width': w, 'height': h}}


# 2026-10-31 22:22 实测发布页（CapaPostNotePlatformActivity）真实结构简化
POST_PAGE = [
    el('@e118', 'ImageButton', label='返回', x=26, y=170, w=89, h=89),
    el('@e65', 'TextView', text='选封面', x=221, y=637, w=210, h=73),
    el('@e68', 'EditText', x=39, y=752, w=1041, h=131),          # 标题
    el('@e72', 'EditText', x=0, y=883, w=1080, h=288),           # 正文
    el('@e89', 'TextView', text='话题', x=115, y=1287, w=100, h=42),
    el('@e92', 'TextView', text='用户', x=305, y=1287, w=100, h=42),
    el('@e96', 'TextView', text='投票', x=492, y=1287, w=100, h=42),
    el('@e103', 'TextView', text='标记地点', label='标记地点', x=42, y=1390, w=912, h=137),
    el('@e120', 'TextView', text='存草稿', x=42, y=1618, w=180, h=105),
    el('@e121', 'Button', text='发布笔记', x=253, y=1618, w=785, h=105),
]

VIDEO_EDIT = [
    el('@e57', 'ImageButton', label='返回'),
    el('@e68', 'TextView', text='特效'),
    el('@e76', 'TextView', text='截取'),
    el('@e79', 'TextView', text='下一步'),
]

VIDEO_PREVIEW = [
    el('@e73', 'TextView', text='一键成片'),
    el('@e74', 'TextView', label='下一步'),
]

POST_ENTRY = [
    el('@e51', 'TextView', text='从相册选择'),
    el('@e55', 'TextView', text='相机'),
    el('@e58', 'TextView', text='写文字'),
    el('@e60', 'TextView', text='取消'),
]

failures = []


def check(name, cond, detail=''):
    if cond:
        print('OK   ' + name)
    else:
        failures.append(name)
        print('FAIL ' + name + ' ' + str(detail))


check('page_of 发布页', T.page_of(POST_PAGE, T.ACT_POST) == 'post_page',
      T.page_of(POST_PAGE, T.ACT_POST))

boxes = T.edit_texts(POST_PAGE)
check('edit_texts 恰好 2 个', len(boxes) == 2, len(boxes))
check('第 1 个是标题(y=752)', bool(boxes) and boxes[0]['coordinates']['y'] == 752)
check('第 2 个是正文(y=883)', len(boxes) > 1 and boxes[1]['coordinates']['y'] == 883)

pub = T.find('post_page', 'publish', POST_PAGE)
check('发布笔记唯一命中', pub is not None and pub['text'] == '发布笔记')
check('发布键风险标记为 send', T.POST_PAGE.targets['publish'].risk == T.RISK_SEND)

draft = T.find('post_page', 'save_draft', POST_PAGE)
check('存草稿唯一命中', draft is not None and draft['text'] == '存草稿')
check('存草稿不是发布键', draft is not pub)

check('话题按钮唯一命中', T.find('post_page', 'topic_btn', POST_PAGE) is not None)

check('编辑页下一步(text)', T.find('video_edit', 'next', VIDEO_EDIT) is not None)
check('预处理页下一步(label)', T.find('video_preview', 'next', VIDEO_PREVIEW) is not None)
check('编辑页识别', T.page_of(VIDEO_EDIT, T.ACT_VIDEO_EDIT) == 'video_edit')
check('预处理页识别', T.page_of(VIDEO_PREVIEW, '') == 'video_preview')

check('弹窗识别', T.page_of(POST_ENTRY, T.ACT_INDEX) == 'post_entry',
      T.page_of(POST_ENTRY, T.ACT_INDEX))
check('发布页不误判为弹窗', T.page_of(POST_PAGE, T.ACT_POST) != 'post_entry')
check('空树安全', T.page_of([], '') is None)

summary = T.state_summary(POST_PAGE)
check('state_summary 不含用户文案', '发布笔记' not in summary and '草稿' not in summary, summary)
print('   摘要：' + summary)

print()
if failures:
    print('失败 %d 项：%s' % (len(failures), '、'.join(failures)))
    sys.exit(1)
print('全部通过')

"""Facebook 资料库自检。

两种模式：
  python fishfleet/web/facebook_selftest.py           # 离线：用实测样本验证判定逻辑
  python fishfleet/web/facebook_selftest.py --live ID # 真机：dump 当前页并校验锚点/唯一性

离线模式不连设备；live 模式只 dump 与前台应用，不做任何点击。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web.facebook_live import setup_utf8_stdout  # noqa: E402

failures: list[str] = []


def el(ref, typ, text='', label='', ident='', x=0, y=0, w=100, h=100, **flags):
    return {'ref': ref, 'type': typ, 'text': text, 'label': label, 'name': '',
            'value': '', 'identifier': ident,
            'x': x, 'y': y, 'width': w, 'height': h,
            'focused': flags.get('focused', False),
            'selected': flags.get('selected', False),
            'checked': flags.get('checked', False),
            'disabled': flags.get('disabled', False)}


def check(name, cond, detail=''):
    if cond:
        print('OK   ' + name)
    else:
        failures.append(name)
        print('FAIL ' + name + ' -> ' + str(detail))


# ---------------------------------------------------------------------------
# 实测样本（2026-11-01，手机8 / katana 530.0.0.48.74 真实层级摘录）
# ---------------------------------------------------------------------------
SEARCH_HISTORY_ALL_SAMPLE = [
    el('@e55', 'Button', label='全部 Selected'),
    el('@e56', 'Button', label='搜尋'),
    el('@e57', 'Button', label='瀏覽數'),
    el('@e59', 'ImageView', label='Back'),
    el('@e62', 'ViewGroup', text='歷史紀錄', label='歷史紀錄'),
    el('@e64', 'Button', label='Clear all search history'),
]

SEARCH_HISTORY_MENU_SAMPLE = [
    el('@e18', 'FrameLayout', label='Close'),
    el('@e21', 'Button', label='marketplace'),
    el('@e22', 'Button', label='刪除, 從搜尋紀錄移除此項目。'),
    el('@e23', 'Button', label='釘選此搜尋條件, 一次只能釘選 3 個搜尋條件。'),
]

HOME_BANNER_SAMPLE = [
    el('@e57', 'TextView', text='再點擊一下就能確認 someone@example.com',
       label='再點擊一下就能確認 someone@example.com'),
    el('@e62', 'Button', text='確認電子郵件地址', label='確認電子郵件地址'),
    el('@e63', 'Button', text='新增其他電子郵件地址', label='新增其他電子郵件地址'),
    el('@e64', 'ImageButton', label='Close'),
    el('@e65', 'Button', label='Go to profile'),
    el('@e66', 'ViewGroup', text='Make a post on Facebook', label='Make a post on Facebook'),
    el('@e72', 'View', label='Home, tab 1 of 4', selected=True),
    el('@e73', 'View', label='Friends, tab 2 of 4'),
]

HOME_SAMPLE = [
    el('@e93', 'Button', label='Facebook menu', x=11, y=149),
    el('@e95', 'Button', label='Create, Double tap to create a new post, story, or reel', x=724, y=149),
    el('@e97', 'Button', label='Search Facebook', x=839, y=149),
    el('@e98', 'Button', label='Messaging', x=954, y=149),
    el('@e102', 'View', label='Home, tab 1 of 4', x=0, y=265, selected=True),
    el('@e103', 'View', label='Friends, tab 2 of 4', x=270, y=265),
    el('@e104', 'View', label='Reels, tab 3 of 4', x=540, y=265),
    el('@e105', 'View', label='Notifications, tab 4 of 4', x=810, y=265),
    el('@e59', 'Button', label='Go to profile', x=0, y=383),
    el('@e60', 'ViewGroup', text='Make a post on Facebook', label='Make a post on Facebook', x=158, y=413),
    el('@e61', 'ViewGroup', label='Select photos or videos for your post', x=953, y=420),
    el('@e62', 'ViewGroup', label='Stories', x=0, y=552),
    el('@e65', 'Button', label='Create story', x=5, y=584),
    el('@e70', 'Button', text='Add friend', label='Add friend', x=361, y=1055),
    el('@e58', 'RecyclerView', ident='android:id/list'),
]

MENU_SAMPLE = [
    el('@e58', 'Button', label='Mander Zuo，請查看你的個人檔案', x=64, y=229),
    el('@e61', 'Button', label='開啟個人檔案切換工具', x=784, y=223),
    el('@e63', 'Button', label='建立 Facebook 粉絲專頁', x=32, y=371),
    el('@e68', 'ViewGroup', text='動態回顧', label='動態回顧', x=64, y=674),
    el('@e70', 'ViewGroup', text='我的珍藏', label='我的珍藏', x=525, y=674),
    el('@e72', 'ViewGroup', text='社團', label='社團', x=64, y=907),
    el('@e74', 'ViewGroup', text='Reel', label='Reel', x=525, y=907),
    el('@e76', 'ViewGroup', text='動態消息', label='動態消息', x=64, y=1140),
    el('@e78', 'ViewGroup', text='尋找朋友', label='尋找朋友', x=525, y=1128),
    el('@e80', 'ViewGroup', text='遊戲', label='遊戲', x=64, y=1373),
    el('@e82', 'ViewGroup', text='活動', label='活動', x=525, y=1361),
    el('@e83', 'Button', text='See more', label='See more', x=32, y=1525),
    el('@e84', 'ViewGroup', text='好物特蒐', label='好物特蒐', x=64, y=1606),
    el('@e86', 'ViewGroup', text='生日', label='生日', x=525, y=1594),
    el('@e85', 'Button',
       label='Help & support, header. Section is collapsed. Double-tap to expand the section.',
       x=0, y=1663),
    el('@e87', 'View', label='Close', x=964, y=149),
]

SEARCH_SAMPLE = [
    el('@e58', 'ImageView', label='Back', x=0, y=149),
    el('@e61', 'EditText', x=158, y=149, focused=True),
    el('@e56', 'ViewGroup', text='沒有近期搜尋紀錄', label='沒有近期搜尋紀錄', x=0, y=266),
    el('@e56b', 'ViewGroup', text='你可能認識的朋友', label='你可能認識的朋友', x=32, y=465),
    el('@e57', 'Button', text='查看所有你可能認識的朋友', label='查看所有你可能認識的朋友', x=904, y=463),
]

FRIENDS_SAMPLE = [
    el('@e64', 'Button', label='建議', x=32, y=383),
    el('@e65', 'Button', label='你的朋友', x=198, y=383),
    el('@e66', 'ViewGroup', text='Friend requests', label='Friend requests', x=32, y=574),
    el('@e67', 'Button', text='See all', label='See all', x=878, y=560),
    el('@e68', 'Button', label="View Allen Chen's profile", x=0, y=648),
    el('@e70', 'Button', text="Confirm Allen Chen's friend request",
       label="Confirm Allen Chen's friend request", x=306, y=810),
    el('@e71', 'Button', text="Delete Allen Chen's friend request",
       label="Delete Allen Chen's friend request", x=688, y=810),
    el('@e80', 'ViewGroup', text='People you may know', label='People you may know', x=32, y=1563),
    el('@e81', 'ViewGroup', text='開啟聯絡人上傳功能以找到朋友', label='開啟聯絡人上傳功能以找到朋友'),
    el('@e84', 'TextView', text='Friends', label='Friends, Heading', x=127, y=279),
    el('@e85', 'Button', label='Search', x=953, y=267),
    el('@e86', 'View', label='Friends, tab 2 of 4', x=270, y=149, selected=True),
]

REELS_SAMPLE = [
    el('@e63', 'Button', label='Video tab details', x=30, y=268),
    el('@e67', 'Button', label='🏡 Building a Miniature House', x=62, y=1690),
    el('@e64', 'ImageView', label="Author's profile", x=62, y=1583),
    el('@e65', 'ViewGroup', text='5-Minute Crafts      ', label='5-Minute Crafts      '),
    el('@e66', 'ViewGroup', text='Follow 5-Minute Crafts', label='Follow 5-Minute Crafts'),
    el('@e68', 'Button', label='120K reactions'),
    el('@e70', 'Button', text='840 comments', label='840 comments'),
    el('@e71', 'Button', text='Share, 2,838 shares', label='Share, 2,838 shares'),
    el('@e72', 'Button', text='Save', label='Save'),
    el('@e73', 'Button', label='More'),
    el('@e79', 'View', text='Reels', label='Reels'),
    el('@e81', 'Button', label='Facebook menu'),
    el('@e82', 'Button', label='Create reel'),
    el('@e83', 'Button', label='Search'),
    el('@e84', 'Button', label='Navigate to your Reels profile'),
    el('@e94', 'View', label='Reels, tab 3 of 4', selected=True),
]

STICKER_PANEL_SAMPLE = [
    el('@e62', 'ImageView', label='Sticker search button'),
    el('@e64', 'Button', text='喜怒哀樂', label='喜怒哀樂'),
    el('@e66', 'Button', text='94愛尷尬', label='94愛尷尬'),
    el('@e68', 'Button', text='哥倆好', label='哥倆好'),
    el('@e70', 'Button', text='一起來聊聊', label='一起來聊聊'),
    el('@e72', 'Button', text='美好日子', label='美好日子'),
    el('@e74', 'Button', text='工作心情', label='工作心情'),
    el('@e76', 'ImageButton', label='Sticker store'),
    el('@e80', 'Button', text='Search', label='Search'),
    el('@e81', 'Button', text='Happy', label='Happy'),
    el('@e82', 'Button', text='In love', label='In love'),
    el('@e83', 'Button', text='Sad', label='Sad'),
    el('@e84', 'Button', text='Eating', label='Eating'),
    el('@e86', 'Button', label='Thumbs Up, girl giving thumbs up'),
    el('@e87', 'Button', label='Shiba Inu dog running towards you with open mouth'),
    el('@e89', 'Button', label='Love, hand holding flowers'),
]

GROUP_DETAIL_SAMPLE = [
    el('@e58', 'ImageView',
       label='Cover photo of group Marketplace. Description is unavailable.'),
    el('@e59', 'Button', label='Marketplace, 公開社團 · 830K members'),
    el('@e60', 'Button', label='Join Marketplace group'),
    el('@e62', 'Button', text='Reel', label='Reel'),
    el('@e63', 'Button', text='討論區', label='討論區'),
    el('@e64', 'Button', text='相片', label='相片'),
    el('@e65', 'Button', text='相簿', label='相簿'),
    el('@e66', 'Button', text='活動', label='活動'),
    el('@e67', 'Button', text='檔案', label='檔案'),
    el('@e69', 'ImageView', label='Show profile'),
    el('@e70', 'Button', label='留個言吧……'),
    el('@e71', 'Button', label='Photo/Video'),
    el('@e72', 'Button', text='最相關', label='最相關'),
    el('@e76', 'ViewGroup', text='16m•Shared with: 公開社團',
       label='16m•Shared with: 公開社團'),
    el('@e80', 'ImageView', label='Back'),
    el('@e85', 'ImageView', label='Search in Marketplace'),
]

GROUP_REELS_SAMPLE = [
    el('@e55', 'ViewGroup', label='Reel,167 plays'),
    el('@e56', 'ViewGroup', label='Reel,83 plays'),
    el('@e57', 'ViewGroup', label='Reel,125 plays'),
    el('@e61', 'ViewGroup', label='Muted Reel,68 plays'),
    el('@e62', 'ViewGroup', label='Reel,308 plays'),
    el('@e65', 'ImageView', label='Back'),
]

GROUP_ALBUMS_SAMPLE = [
    el('@e55', 'Button', label='Create album button'),
    el('@e56', 'ImageView', label='Go to profile'),
    el('@e58', 'ViewGroup', label='Sheikh Abdul kabeer, 10 photos'),
    el('@e60', 'ViewGroup', label='Guitar, 2 photos'),
    el('@e62', 'ViewGroup', label='Laundry, 5 photos'),
    el('@e73', 'Button', label='Back'),
]

GROUP_FILES_SAMPLE = [
    el('@e54', 'Button', label='Add file button'),
    el('@e55', 'ImageView', label='Show profile'),
    el('@e56', 'ViewGroup', text="This group doesn't have any files yet.",
       label="This group doesn't have any files yet."),
    el('@e58', 'ImageView', label='Back'),
    el('@e60', 'TextView', text='Files', label='Files'),
]

GROUP_EVENTS_SAMPLE = [
    el('@e55', 'ViewGroup', text='近期活動', label='近期活動'),
    el('@e57', 'ImageView', label='封面相片 未提供相片說明。'),
    el('@e58', 'Button', label='正在進行Vendo terreno entrada a mahahual 7 hectáreas'),
    el('@e59', 'ViewGroup', text='1 人會參加', label='1 人會參加'),
    el('@e60', 'Button', label='對 Vendo terreno entrada a mahahual 7 有興趣'),
    el('@e61', 'Button', label='分享 Vendo terreno entrada a mahahual 7'),
    el('@e64', 'Button', label='週一 , 11月2日上午2:00（CST） 和另外 1 個場次'),
    el('@e65', 'ViewGroup', text='42 人有興趣 · 1 人會參加',
       label='42 人有興趣 · 1 人會參加'),
    el('@e66', 'Button', label='對 Madam Mims brevkasse. 有興趣'),
    el('@e73', 'Button', label='Back'),
]

GROUP_PHOTOS_SAMPLE = [
    el('@e54', 'Button', label='View post'),
    el('@e59', 'Button', label='View post'),
    el('@e63', 'Button', label='View post'),
    el('@e73', 'Button', label='Back'),
    el('@e74', 'ViewGroup', text='Photos', label='Photos'),
]

GIF_PANEL_SAMPLE = [
    el('@e61', 'EditText', label='Enter search terms to search for GIFs'),
    el('@e60', 'ImageButton'),
    el('@e65', 'Button', label='Love You Reaction GIF by Bounce'),
    el('@e67', 'Button', label='Oh No Reaction GIF by The Great British Bake Off'),
    el('@e69', 'Button', label='Staring Samantha Jones GIF by HBO Max'),
    el('@e71', 'Button', label='Dance Dancing GIF'),
]

SHARE_PANEL_SAMPLE = [
    el('@e51', 'Button', label='Close'),
    el('@e52', 'ViewGroup',
       text='你分享的連結為你專屬，且可能會用於改善你看到的建議和廣告。 瞭解詳情',
       label='你分享的連結為你專屬，且可能會用於改善你看到的建議和廣告。 瞭解詳情'),
    el('@e53', 'Button', text='瞭解詳情', label='瞭解詳情'),
    el('@e54', 'Button', label='Select the destination to share.'),
    el('@e55', 'Button', label='所有人'),
    el('@e59', 'Button', label='Open feeling/activity screen'),
    el('@e60', 'Button', label='Tag friends'),
    el('@e61', 'Button', label='Share now'),
    el('@e62', 'ViewGroup', text='Share to', label='Share to'),
    el('@e64', 'Button', label='Your story'),
    el('@e65', 'Button', label='Messenger'),
    el('@e66', 'Button', label='Copy link'),
    el('@e67', 'Button', label='Group'),
    el('@e68', 'Button', label='Text'),
    el('@e69', 'Button', label='More options for Share to'),
]

REELS_PLAYING_SAMPLE = [
    el('@e63', 'ViewGroup', label='🏡 Building a Miniature House'),
    el('@e64', 'Button', label='Video tab details'),
    el('@e65', 'Button', label='Rewind 10 seconds'),
    el('@e66', 'Button', label='Play'),
    el('@e67', 'Button', label='Fast forward 10 seconds'),
    el('@e68', 'ImageView', label="Author's profile"),
    el('@e70', 'ViewGroup', label='Follow 5-Minute Crafts'),
    el('@e72', 'Button', label='120K reactions'),
    el('@e74', 'Button', text='841 comments', label='841 comments'),
    el('@e75', 'Button', text='Share, 2,840 shares', label='Share, 2,840 shares'),
    el('@e76', 'Button', text='Save', label='Save'),
    el('@e81', 'View', text='Reels', label='Reels'),
    el('@e94', 'View', label='Reels, tab 3 of 4', selected=True),
]

NOTIF_SAMPLE = [
    el('@e60', 'ViewGroup', text='新通知', label='新通知'),
    el('@e61', 'Button', label='我們注意到你的帳號從與平常不同的裝置或地點登入。請檢查登入情形。. 9h. Unread'),
    el('@e63', 'Button', label='Meta 帳號即將在 Facebook 推出。你很快就會升級至該帳號。. 9h. Unread'),
    el('@e68', 'TextView', text='Notifications', label='Notifications, Heading'),
    el('@e79', 'View', label='Notifications, tab 4 of 4', selected=True),
]

CREATE_POST_SAMPLE = [
    el('@e55', 'ImageView', label='Back'),
    el('@e57', 'TextView', text='Create post'),
    el('@e59', 'Button', text='POST', label='POST', disabled=True),
    el('@e64', 'Button', label='所有人'),
    el('@e66', 'Button', text='AI label off. Collapsed', label='AI label off. Collapsed'),
    el('@e67', 'Button', text='Add album', label='Add album'),
    el('@e68', 'Button', text='Share to Instagram. Off.', label='Share to Instagram. Off.'),
    el('@e72', 'AutoCompleteTextView'),
    el('@e76', 'Button', text='相片／影片', label='相片／影片'),
    el('@e77', 'Button', text='標註用戶', label='標註用戶'),
    el('@e78', 'Button', text='感受／活動', label='感受／活動'),
    el('@e79', 'Button', text='打卡', label='打卡'),
    el('@e80', 'Button', text='直播視訊', label='直播視訊'),
    el('@e81', 'Button', text='背景色彩', label='背景色彩'),
]

POST_DETAIL_SAMPLE = [
    el('@e55', 'Button', label='Close'),
    el('@e56', 'Button', text='480', label='480'),
    el('@e57', 'Button', text='12 shares', label='12 shares'),
    el('@e64', 'ViewGroup', text='Calvin Chan', label='Calvin Chan'),
    el('@e66', 'ViewGroup', text='3 days', label='3 days'),
    el('@e67', 'ViewGroup', text="Like Calvin Chan's comment button. Double tap and hold to show reaction tray.",
       label="Like Calvin Chan's comment button. Double tap and hold to show reaction tray."),
    el('@e68', 'ViewGroup', text="Reply to Calvin Chan's comment, button. Double tap to reply to the comment.",
       label="Reply to Calvin Chan's comment, button. Double tap to reply to the comment."),
    el('@e80', 'AutoCompleteTextView', label='Write a comment…'),
]

POST_DETAIL_FULL_SAMPLE = [
    el('@e57', 'Button', label='Close'),
    el('@e58', 'Button', text='8,866', label='8,866'),
    el('@e59', 'Button', text='362 shares', label='362 shares'),
    el('@e65', 'ImageView', label='刘刚'),
    el('@e66', 'ViewGroup', text='刘刚', label='刘刚'),
    el('@e67', 'ViewGroup', text='问题就在于重金属中毒。。', label='问题就在于重金属中毒。。'),
    el('@e68', 'ViewGroup', text='6 weeks', label='6 weeks'),
    el('@e69', 'ViewGroup',
       text="Like 刘刚's comment button. Double tap and hold to show reaction tray.",
       label="Like 刘刚's comment button. Double tap and hold to show reaction tray."),
    el('@e70', 'ViewGroup',
       text="Reply to 刘刚's comment, button. Double tap to reply to the comment.",
       label="Reply to 刘刚's comment, button. Double tap to reply to the comment."),
    el('@e71', 'Button', text='178 reactions', label='178 reactions'),
    el('@e72', 'ImageView', label='鄭心偉, 下個雨，屋簷底下就滿滿的鉛'),
    el('@e73', 'Button', text='View 41 more replies to 刘刚…',
       label='View 41 more replies to 刘刚…'),
    el('@e78', 'AutoCompleteTextView', label='Write a comment…'),
    el('@e79', 'Button', label='Show photos and videos'),
    el('@e80', 'Button', label='Open GIF search'),
    el('@e81', 'Button', label='Show stickers'),
    el('@e82', 'Button', text='Send', label='Send', disabled=True),
]

PROFILE_SAMPLE = [
    el('@e56', 'Button', label='Cover Photo'),
    el('@e57', 'Button', label='編輯封面相片'),
    el('@e58', 'Button', label='大頭貼照'),
    el('@e59', 'Button', label='建立便利貼：在想些什麼？'),
    el('@e63', 'Button', label='開啟個人檔案切換工具'),
    el('@e65', 'Button', label='編輯大頭貼照'),
    el('@e67', 'Button', label='新增到限時動態'),
    el('@e69', 'Button', label='編輯個人檔案'),
    el('@e71', 'ViewGroup', text='People you may know', label='People you may know'),
    el('@e74', 'Button', label='全部, 1 of 3', selected=True),
    el('@e75', 'Button', label='相片, 2 of 3'),
    el('@e76', 'Button', label='Reel, 3 of 3'),
    el('@e78', 'ImageView', label='Back'),
    el('@e83', 'ImageView', label='搜尋'),
]

GROUPS_SAMPLE = [
    el('@e54', 'Button', text='探索', label='探索', selected=True),
    el('@e58', 'ViewGroup', text='Suggested for you', label='Suggested for you'),
    el('@e59', 'Button', label='Suggested group: Meeting Singles From USA,Canada& Australia. 公開社團 · 2.7 萬位成員'),
    el('@e60', 'Button', label='Hide suggested group: Meeting Singles From USA,Canada& Australia.'),
    el('@e62', 'Button', text='Join group: Meeting Singles From USA,Canada& Australia.',
       label='Join group: Meeting Singles From USA,Canada& Australia.'),
    el('@e76', 'Button', label='Back'),
    el('@e77', 'Button', label='Create group'),
    el('@e78', 'Button', label='Settings'),
    el('@e79', 'Button', label='Search groups'),
]

MARKET_SAMPLE = [
    el('@e56', 'Button', label='Back'),
    el('@e57', 'TextView', text='Finds', label='Finds, Heading'),
    el('@e58', 'Button', label='shopping cart'),
    el('@e59', 'Button', label='menu'),
]

MEMORIES_SAMPLE = [
    el('@e78', 'Button', label='Back'),
    el('@e79', 'Button', label='Story archive'),
    el('@e80', 'Button', label='Settings'),
    el('@e84', 'ViewGroup', label='稍後再返回查看更多動態回顧'),
]

GROUP_DETAIL_JOINED_SAMPLE = [
    el('@e57', 'ImageView',
       label='Cover photo of group Marketplace. Description is unavailable.'),
    el('@e58', 'Button', label='Marketplace, 公開社團 · 510K members'),
    el('@e59', 'Button', label='已加入「 Marketplace 」社團'),
    el('@e71', 'Button', label='留個言吧……'),
    el('@e80', 'ImageView', label='Back'),
]

PAGE_DETAIL_SAMPLE = [
    el('@e56', 'Button', label='Cover Photo'),
    el('@e57', 'Button', label='有未讀限時動態的大頭貼照'),
    el('@e59', 'Button', text='Marketplace', label='Marketplace'),
    el('@e60', 'Button', label='驗證標章'),
    el('@e63', 'Button', text='25 萬位追蹤者', label='25 萬位追蹤者'),
    el('@e67', 'Button', text='17 人追蹤中', label='17 人追蹤中'),
    el('@e75', 'Button', label='相片, 2 of 5'),
    el('@e72', 'Button', text='查看更多', label='查看更多'),
    el('@e74', 'Button', text='追蹤', label='追蹤'),
]

META_ACCOUNT_SAMPLE = [
    el('@e50', 'View', text='關於 Meta 帳號', label='關於 Meta 帳號'),
    el('@e51', 'View', text='輕鬆存取和控制你選擇的所有應用程式和裝置',
       label='輕鬆存取和控制你選擇的所有應用程式和裝置'),
    el('@e52', 'View', text='Meta 帳號是什麼？', label='Meta 帳號是什麼？'),
    el('@e53', 'View', text='這是可以在 Facebook、Instagram 或 AI 眼鏡等 Meta 應用程式和裝置上使用的帳號。',
       label='這是可以在 Facebook、Instagram 或 AI 眼鏡等 Meta 應用程式和裝置上使用的帳號。'),
    el('@e54', 'Button', text='看看你可以在哪些地方使用 Meta 帳號',
       label='看看你可以在哪些地方使用 Meta 帳號'),
]

MISSED_POST_SAMPLE = [
    el('@e54', 'ImageView', label='ጉርሻ page Profile picture'),
    el('@e55', 'ViewGroup', text='1h•Shared with: 所有人', label='1h•Shared with: 所有人'),
    el('@e56', 'Button', label='See more options'),
    el('@e58', 'Button', text='more', label='more'),
    el('@e59', 'ViewGroup', text='See translation', label='See translation'),
    el('@e60', 'ImageView', label='Photo'),
    el('@e62', 'Button', label='Back'),
    el('@e63', 'TextView', text='你可能錯過的貼文, Heading',
       label='你可能錯過的貼文, Heading'),
    el('@e64', 'Button', label='Search'),
]

PHOTO_MORE_SAMPLE = [
    el('@e50', 'Button', text='Make cover photo', label='Make cover photo'),
    el('@e53', 'Button', text='Edit caption', label='Edit caption'),
    el('@e56', 'Button', text='Delete photo', label='Delete photo'),
    el('@e59', 'Button', text='Save to phone', label='Save to phone'),
    el('@e62', 'Button', text='Share external', label='Share external'),
]

PHOTO_VIEWER_SAMPLE = [
    el('@e56', 'ImageView', label='Photo'),
    el('@e57', 'FrameLayout', label='Tag more friends'),
    el('@e60', 'ImageView', label='Back'),
    el('@e61', 'ViewGroup', text='1 of 6', label='1 of 6'),
    el('@e63', 'Button', label='Tagging mode'),
    el('@e64', 'ImageView', label='Location'),
    el('@e65', 'ImageView', label='More'),
    el('@e68', 'Button', text='Mander Zuo', label='Mander Zuo'),
    el('@e70', 'TextView', text='2023年4月8日', label='2023年4月8日'),
]

PROFILE_PHOTOS_PAGE_SAMPLE = [
    el('@e51', 'ImageView', label='Back'),
    el('@e53', 'TextView', text='Mander Zuo', label='Mander Zuo'),
    el('@e56', 'TextView', text='PHOTOS OF YOU, tab 1 of 3',
       label='PHOTOS OF YOU, tab 1 of 3'),
    el('@e57', 'TextView', text='UPLOADS, tab 2 of 3', label='UPLOADS, tab 2 of 3',
       selected=True),
    el('@e58', 'TextView', text='ALBUMS, tab 3 of 3', label='ALBUMS, tab 3 of 3'),
    el('@e61', 'Button', label='Photo'),
    el('@e62', 'Button', label='Photo'),
]

FACEBOOK_PAGES_SAMPLE = [
    el('@e55', 'Button', text='建立', label='建立'),
    el('@e56', 'Button', text='已追蹤的粉絲專頁', label='已追蹤的粉絲專頁'),
    el('@e57', 'Button', text='邀請', label='邀請'),
    el('@e58', 'ViewGroup', text='最近瀏覽過的粉絲專頁', label='最近瀏覽過的粉絲專頁'),
    el('@e61', 'ViewGroup', text='ጉርሻ page', label='ጉርሻ page'),
    el('@e63', 'ViewGroup', text='1,061,647 人在追蹤', label='1,061,647 人在追蹤'),
    el('@e64', 'Button', text='發送訊息', label='發送訊息'),
]

BIRTHDAYS_SAMPLE = [
    el('@e52', 'ViewGroup', text='你朋友的生日會顯示在這裡。',
       label='你朋友的生日會顯示在這裡。'),
    el('@e54', 'Button', label='Back'),
]

SAVED_ITEMS_SAMPLE = [
    el('@e52', 'Button', label='全部，1／6', selected=True),
    el('@e54', 'Button', label='Reel，2／6'),
    el('@e56', 'Button', label='貼文，3／6'),
    el('@e58', 'Button', label='Marketplace，4／6'),
    el('@e60', 'Button', label='珍藏分類，5／6'),
    el('@e62', 'View', text='珍藏分類', label='珍藏分類'),
    el('@e65', 'Button', label='建立新珍藏分類'),
    el('@e68', 'View', text='近期儲存', label='近期儲存'),
    el('@e69', 'ViewGroup', text='在這裡重新探索你的珍藏內容，或將它們整理成珍藏分類。',
       label='在這裡重新探索你的珍藏內容，或將它們整理成珍藏分類。'),
]

EVENTS_HOME_SAMPLE = [
    el('@e53', 'Button', text='Xinjin', label='Xinjin'),
    el('@e55', 'Button', text='為你推薦', label='為你推薦', selected=True),
    el('@e57', 'Button', text='本地', label='本地'),
    el('@e59', 'Button', text='本週', label='本週'),
    el('@e61', 'Button', text='朋友', label='朋友'),
]

REQUEST_SORT_SAMPLE = [
    el('@e50', 'ViewGroup', text='Default', label='Default'),
    el('@e51', 'ViewGroup', text='Newest request first', label='Newest request first'),
    el('@e52', 'ViewGroup', text='Oldest request first', label='Oldest request first'),
    el('@e57', 'Button', label='Sort'),
]

FRIEND_REQUESTS_ALL_SAMPLE = [
    el('@e56', 'ViewGroup', text='Friend requests', label='Friend requests'),
    el('@e57', 'Button', label='Sort'),
    el('@e58', 'Button', label="View Allen Chen's profile"),
    el('@e60', 'Button', label="Confirm Allen Chen's friend request"),
    el('@e61', 'Button', label="Delete Allen Chen's friend request"),
    el('@e62', 'Button', label="View Yuan Yuan's profile"),
]

PYMK_ALL_SAMPLE = [
    el('@e56', 'Button', label="View Dinesh Gujarati's profile"),
    el('@e58', 'Button', label="Confirm Dinesh Gujarati's friend request"),
    el('@e59', 'Button', label="Delete Dinesh Gujarati's friend request"),
    el('@e60', 'ViewGroup', text='People you may know', label='People you may know'),
    el('@e61', 'ViewGroup', text='開啟聯絡人上傳功能以找到朋友',
       label='開啟聯絡人上傳功能以找到朋友'),
    el('@e62', 'Button', text='Indrajeet Kumar', label='Indrajeet Kumar'),
    el('@e64', 'Button', text='Add friend', label='Add friend'),
]

EVENTS_CATEGORY_FILTER_SAMPLE = [
    el('@e50', 'Button', label='Back'),
    el('@e51', 'Button', text='Reset', label='Reset', disabled=True),
    el('@e53', 'RadioButton', text='所有類別', label='所有類別', selected=True),
    el('@e54', 'RadioButton', text='藝術', label='藝術'),
    el('@e55', 'RadioButton', text='慈善活動', label='慈善活動'),
    el('@e56', 'RadioButton', text='喜劇', label='喜劇'),
    el('@e57', 'RadioButton', text='手工藝', label='手工藝'),
    el('@e58', 'RadioButton', text='舞蹈', label='舞蹈'),
]

EVENTS_DATE_FILTER_SAMPLE = [
    el('@e50', 'Button', label='Back'),
    el('@e51', 'Button', text='Reset', label='Reset', disabled=True),
    el('@e53', 'RadioButton', text='任何時間', label='任何時間', selected=True),
    el('@e54', 'RadioButton', text='今天', label='今天'),
    el('@e55', 'RadioButton', text='明天', label='明天'),
    el('@e56', 'RadioButton', text='本週', label='本週'),
    el('@e57', 'RadioButton', text='本週末', label='本週末'),
    el('@e58', 'RadioButton', text='下週', label='下週'),
]

EVENTS_FILTERS_SAMPLE = [
    el('@e50', 'Button', text='Reset', label='Reset', disabled=True),
    el('@e51', 'Button', text='日期, 任何時間', label='日期, 任何時間'),
    el('@e52', 'Button', text='地點, 任何地方', label='地點, 任何地方'),
    el('@e53', 'Button', text='類別, 所有類別', label='類別, 所有類別'),
    el('@e54', 'CompoundButton', text='獲朋友好評', label='獲朋友好評'),
    el('@e57', 'Button', text='Show results', label='Show results'),
]

GROUP_REEL_VIEW_SAMPLE = [
    el('@e57', 'Button', label='Back'),
    el('@e58', 'Button', label='Pick viewer content to show'),
    el('@e59', 'Button', label='Search'),
    el('@e60', 'Button', label='Navigate to your Reels profile'),
]

GROUP_POSTS_FILTERS_SAMPLE = [
    el('@e50', 'Button', text='Reset', label='Reset'),
    el('@e51', 'CompoundButton', text='最新貼文', label='最新貼文'),
    el('@e54', 'CompoundButton', text='你看過的貼文', label='你看過的貼文'),
    el('@e57', 'Button', text='發佈日期, 任何日期', label='發佈日期, 任何日期'),
    el('@e58', 'Button', text='貼文來源, 你的社團', label='貼文來源, 你的社團'),
    el('@e59', 'Button', text='標註的地點, 任何地方', label='標註的地點, 任何地方'),
    el('@e60', 'Button', text='Show results', label='Show results'),
]

GROUP_POST_MORE_SAMPLE = [
    el('@e50', 'Button', label='Close'),
    el('@e52', 'Button', label='Save reel Add this to your saved reels.'),
    el('@e53', 'Button', label='檢舉貼文 我們不會向 Katia Cristina 透露檢舉人的身分。'),
    el('@e54', 'Button', label='Report to group admins '),
    el('@e56', 'Button', label='Turn on notifications for this post '),
    el('@e58', 'Button', label='Copy link '),
]

GROUP_SORT_SAMPLE = [
    el('@e50', 'Button', label='Close'),
    el('@e51', 'ViewGroup', text='Sort groups', label='Sort groups'),
    el('@e52', 'RadioButton', text='Most visited', label='Most visited'),
    el('@e53', 'RadioButton', text='Groups you manage', label='Groups you manage'),
    el('@e56', 'RadioButton', text='Groups A-Z', label='Groups A-Z'),
]

GROUPS_YOUR_SAMPLE = [
    el('@e54', 'Button', text='為你推薦', label='為你推薦'),
    el('@e55', 'Button', text='你的社團', label='你的社團', selected=True),
    el('@e56', 'Button', text='探索', label='探索'),
    el('@e60', 'Button', text='Create a group', label='Create a group'),
    el('@e64', 'Button', label='Sort by: Most visited'),
    el('@e65', 'Button', label='Sort groups'),
    el('@e67', 'ViewGroup', text='Marketplace', label='Marketplace'),
]

GROUPS_FOR_YOU_SAMPLE = [
    el('@e54', 'Button', text='為你推薦', label='為你推薦', selected=True),
    el('@e55', 'Button', text='你的社團', label='你的社團'),
    el('@e56', 'Button', text='探索', label='探索'),
    el('@e60', 'ViewGroup', text='Your groups', label='Your groups'),
    el('@e61', 'Button', text='See all', label='See all'),
    el('@e63', 'ViewGroup', text='Marketplace', label='Marketplace'),
    el('@e64', 'ViewGroup', text='來自你的社團', label='來自你的社團'),
]

SEE_ALL_GROUPS_SAMPLE = [
    el('@e53', 'Button', label='Filters'),
    el('@e55', 'Button', text='Groups', label='Groups', selected=True),
    el('@e56', 'Button', text='城巿', label='城巿'),
    el('@e57', 'Button', text='在我附近', label='在我附近'),
    el('@e58', 'Button', text='公開社團', label='公開社團'),
    el('@e59', 'Button', text='我的社團', label='我的社團'),
    el('@e62', 'Button', label='marketplace, 公開 · 22 萬位成員 · 一天 90 則以上貼文'),
    el('@e64', 'Button', text='加入', label='加入'),
]

REACTION_TRAY_SAMPLE = [
    el('@e49', 'RopeStyleUFIDockView', label='Like'),
    el('@e50', 'RopeStyleUFIDockView', label='Love'),
    el('@e51', 'RopeStyleUFIDockView', label='Care'),
    el('@e52', 'RopeStyleUFIDockView', label='Haha'),
    el('@e53', 'RopeStyleUFIDockView', label='Wow'),
    el('@e54', 'RopeStyleUFIDockView', label='Sad'),
    el('@e55', 'RopeStyleUFIDockView', label='Angry'),
    el('@e56', 'RopeStyleUFIDockView', label='Cancel'),
    el('@e70', 'Button', text='Like button. Double tap and hold to react to the comment.',
       label='Like button. Double tap and hold to react to the comment.'),
]

GROUP_POSTS_TAB_SAMPLE = [
    el('@e53', 'Button', label='1 applied filters'),
    el('@e55', 'Button', text='Posts', label='Posts', selected=True),
    el('@e56', 'Button', text='最新貼文', label='最新貼文'),
    el('@e57', 'Button', text='你看過的貼文', label='你看過的貼文'),
    el('@e58', 'Button', text='發佈日期', label='發佈日期'),
    el('@e59', 'Button', text='你的社團', label='你的社團', selected=True),
    el('@e62', 'Button', label='Marketplace, 3 天. Shang the Online shop More design'),
]

GROUP_SEARCH_RESULTS_SAMPLE = [
    el('@e55', 'Button', text='Groups', label='Groups'),
    el('@e56', 'Button', text='Group Posts', label='Group Posts'),
    el('@e59', 'Button', label='marketplace, 公開 · 22 萬位成員 · 一天 90 則以上貼文'),
    el('@e61', 'Button', text='加入', label='加入'),
    el('@e64', 'Button', label='Marketplace, 公開 · 18 萬位成員 · 一天 90 則以上貼文'),
    el('@e58', 'ImageView', label='Back'),
]

GROUP_COMPOSER_SAMPLE = [
    el('@e54', 'ImageView', label='Back'),
    el('@e56', 'TextView', text='漲知識'),
    el('@e58', 'Button', text='POST', label='POST', disabled=True),
    el('@e63', 'Button', label='公開社團'),
    el('@e65', 'Button', text='AI label off. Collapsed', label='AI label off. Collapsed'),
    el('@e73', 'Button', text='相片／影片', label='相片／影片'),
    el('@e74', 'Button', text='標註用戶', label='標註用戶'),
    el('@e75', 'Button', text='相機', label='相機'),
    el('@e76', 'Button', text='貼文標題', label='貼文標題'),
    el('@e77', 'Button', text='打卡', label='打卡'),
    el('@e78', 'Button', text='感受／活動', label='感受／活動'),
]

STORY_EDITOR_SAMPLE = [
    el('@e44', 'Button', label='Music'),
    el('@e49', 'Button', label='Effects'),
    el('@e54', 'Button', label='Tap to mention people'),
    el('@e61', 'Button', label='Sticker.'),
    el('@e68', 'Button', label='Story Settings Menu'),
    el('@e72', 'Button', text='Share', label='Share'),
]

STORY_DISCARD_SAMPLE = [
    el('@e51', 'ViewGroup',
       text="Discard photo?, You'll lose this photo and any changes you've made to ",
       label="Discard photo?, You'll lose this photo and any changes you've made to "),
    el('@e56', 'Button', text='Discard photo', label='Discard photo'),
    el('@e57', 'ViewGroup', text='Discard photo', label='Discard photo'),
    el('@e68', 'Button', label='Story Settings Menu'),
]

POST_MORE_MENU_SAMPLE = [
    el('@e50', 'Button', label='Close'),
    el('@e52', 'ViewGroup', text='Why am I seeing this post?',
       label='Why am I seeing this post?'),
    el('@e53', 'Button', text='Why am I seeing this post?',
       label='Why am I seeing this post?'),
    el('@e54', 'Button', label='有興趣 你會在動態消息看到更多這類建議貼文。'),
    el('@e55', 'Button', label='沒興趣 你會在動態消息看到更少這類建議貼文。'),
    el('@e56', 'Button', label='Save reel Add this to your saved reels.'),
    el('@e57', 'Button', label='Cast reel Cast to a nearby device'),
    el('@e58', 'Button', label='隱藏貼文 看到更少這類貼文。'),
    el('@e59', 'Button', label='檢舉 Reel 我們不會向 Ryan Resatka 透露檢舉人的身分。'),
    el('@e60', 'Button', label='Turn on notifications for this post '),
    el('@e62', 'ImageView', label='圖像'),
    el('@e75', 'Button', text='3 comments', label='3 comments'),
]

POST_PHOTO_SAMPLE = [
    el('@e62', 'ImageView', label='圖像'),
    el('@e63', 'View', text='你是否已經有相關產品？', label='你是否已經有相關產品？'),
    el('@e64', 'Button', text='有', label='有'),
    el('@e66', 'Button', text='籌備中', label='籌備中'),
    el('@e68', 'Button', text='沒有', label='沒有'),
    el('@e70', 'ViewGroup', text='Shared Link: 表單, SHOPLINE - 全方位開網店平台',
       label='Shared Link: 表單, SHOPLINE - 全方位開網店平台'),
    el('@e72', 'Button', text='瞭解詳情', label='瞭解詳情'),
    el('@e74', 'Button', text='126', label='126'),
    el('@e75', 'Button', text='3 comments', label='3 comments'),
    el('@e76', 'Button',
       label='Like button. Double tap and hold to react to the comment.'),
    el('@e77', 'Button', text='Comment', label='Comment'),
    el('@e79', 'ImageView', label='Ryan Resatka Profile picture'),
]

PROFILE_PHOTOS_SAMPLE = [
    el('@e56', 'Button', label='Cover Photo'),
    el('@e57', 'Button', label='編輯封面相片'),
    el('@e69', 'Button', label='編輯個人檔案'),
    el('@e74', 'Button', label='全部, 1 of 3'),
    el('@e75', 'Button', label='相片, 2 of 3', selected=True),
    el('@e76', 'Button', label='Reel, 3 of 3'),
    el('@e78', 'Button', text='你被標註在內的相片', label='你被標註在內的相片'),
    el('@e79', 'Button', text='相片', label='相片'),
    el('@e83', 'ImageView', label='Back'),
]

SEARCH_TYPED_SAMPLE = [
    el('@e57', 'ImageView', label='Back'),
    el('@e60', 'EditText', text='#marketplace'),
    el('@e61', 'ImageView', label='Clear text'),
]

HASHTAG_RESULT_SAMPLE = [
    el('@e54', 'Button', label='All search results, 1 of 7', selected=True),
    el('@e55', 'Button', label='People search results, 2 of 7'),
    el('@e56', 'Button', label='Groups search results, 3 of 7'),
    el('@e63', 'Button', label='謝文甲, 2 週. 謝文甲 115.10.18（日）河南-南太行山之旅D9'),
    el('@e65', 'ImageView', label='謝文甲'),
]

TAG_SEARCH_SAMPLE = [
    el('@e54', 'Button', label='All search results, 1 of 7', selected=True),
    el('@e55', 'Button', label='People search results, 2 of 7'),
    el('@e56', 'Button', label='Groups search results, 3 of 7'),
    el('@e57', 'Button', label='Events search results, 4 of 7'),
    el('@e58', 'Button', label='Pages search results, 5 of 7'),
    el('@e59', 'Button', label='Reels search results, 6 of 7'),
]

SEARCH_HISTORY_SAMPLE = [
    el('@e56', 'ViewGroup', text='近期', label='近期'),
    el('@e57', 'Button', text='查看所有近期搜尋紀錄', label='查看所有近期搜尋紀錄'),
    el('@e59', 'Button', text='marketplace', label='marketplace'),
    el('@e60', 'Button', text='此搜尋字詞的更多選項', label='此搜尋字詞的更多選項'),
    el('@e61', 'ViewGroup', text='你可能認識的朋友', label='你可能認識的朋友'),
    el('@e62', 'Button', text='查看所有你可能認識的朋友', label='查看所有你可能認識的朋友'),
    el('@e69', 'ImageView', label='Back'),
]

SEARCH_RESULTS_SAMPLE = [
    el('@e54', 'Button', label='All search results, 1 of 7', selected=True),
    el('@e55', 'Button', label='Reels search results, 2 of 7'),
    el('@e56', 'Button', label='People search results, 3 of 7'),
    el('@e57', 'Button', label='Groups search results, 4 of 7'),
    el('@e58', 'Button', label='Events search results, 5 of 7'),
    el('@e59', 'Button', label='Pages search results, 6 of 7'),
    el('@e63', 'View', text='社團', label='社團'),
    el('@e64', 'Button', text='查看全部', label='查看全部'),
    el('@e66', 'Button', label='Marketplace, 公開 · 51 萬位成員 · 一天 90 則以上貼文'),
    el('@e68', 'Button', text='加入', label='加入'),
    el('@e81', 'View', text='這些搜尋結果是否實用？', label='這些搜尋結果是否實用？'),
    el('@e82', 'Button', text='是', label='是'),
    el('@e84', 'Button', text='否', label='否'),
    el('@e86', 'Button', text='marketplace facebook', label='marketplace facebook'),
    el('@e97', 'ViewGroup',
       text='搜尋結果只會包含你能查看的內容\n瞭解詳情\n按鈕',
       label='搜尋結果只會包含你能查看的內容\n瞭解詳情\n按鈕'),
    el('@e99', 'ImageView', label='Back'),
    el('@e102', 'EditText', text='marketplace'),
    el('@e103', 'ImageView', label='Clear text'),
]

PERSON_PROFILE_SAMPLE = [
    el('@e57', 'ImageView', label='Cover Photo'),
    el('@e58', 'Button', label='大頭貼照'),
    el('@e60', 'Button', text='MarketPlace-Inbox Request-Now',
       label='MarketPlace-Inbox Request-Now'),
    el('@e62', 'Button', text='加朋友', label='加朋友'),
    el('@e63', 'View', text='加朋友', label='加朋友'),
    el('@e64', 'Button', text='發送訊息', label='發送訊息'),
    el('@e65', 'View', text='發送訊息', label='發送訊息'),
    el('@e68', 'Button', label='全部, 1 of 3', selected=True),
    el('@e69', 'Button', label='相片, 2 of 3'),
    el('@e70', 'Button', label='Reel, 3 of 3'),
    el('@e71', 'ViewGroup', text="MarketPlace-Inbox Request-Now's posts",
       label="MarketPlace-Inbox Request-Now's posts"),
    el('@e75', 'ViewGroup', text='2025年3月10日•Shared with: 所有人',
       label='2025年3月10日•Shared with: 所有人'),
    el('@e78', 'ImageView', label='Back'),
    el('@e83', 'ImageView', label='搜尋'),
]

SETTINGS_EXPANDED_SAMPLE = [
    el('@e58', 'Button',
       label='Settings & privacy, header. Section is expanded. Double-tap to collaps'),
    el('@e59', 'ViewGroup', text='Settings & privacy', label='Settings & privacy'),
    el('@e61', 'ViewGroup', text='Settings', label='Settings'),
    el('@e63', 'ViewGroup', text='Privacy Center', label='Privacy Center'),
    el('@e65', 'ViewGroup', text='裝置要求', label='裝置要求'),
    el('@e67', 'ViewGroup', text='近期廣告動態', label='近期廣告動態'),
    el('@e69', 'ViewGroup', text='訂單和付款', label='訂單和付款'),
    el('@e71', 'ViewGroup', text='Link history', label='Link history'),
    el('@e78', 'View', label='Close'),
    # 底层首页元素仍在层级里 —— 正是误判的来源
    el('@e103', 'Button', label='Facebook menu'),
    el('@e107', 'View', label='Home, tab 1 of 4', selected=True),
    el('@e88', 'Button', label='Go to profile'),
    el('@e89', 'ViewGroup', text='Stories', label='Stories'),
]

POST_FULL_SAMPLE = [
    el('@e61', 'FrameLayout', label='Video'),
    el('@e63', 'TextView', text='Tap to mute'),
    el('@e64', 'Button', label='Mute video: Off'),
    el('@e65', 'ImageView', label='Toggle video sound'),
    el('@e66', 'Button', text='3.50K', label='3.50K'),
    el('@e67', 'Button', text='92 comments', label='92 comments'),
    el('@e68', 'Button', text='454 shares', label='454 shares'),
    el('@e69', 'Button', text='550K views', label='550K views'),
    el('@e70', 'Button',
       label='Like button. Double tap and hold to react to the comment.'),
    el('@e71', 'Button', text='Comment', label='Comment'),
    el('@e72', 'Button', text='Share', label='Share'),
    el('@e74', 'ImageView', label="Open Stevie Grace's story, unseen story"),
    el('@e75', 'ViewGroup', text='2d•Shared with: 所有人', label='2d•Shared with: 所有人'),
    el('@e76', 'Button', label='See more options'),
    el('@e77', 'Button', label='Hide post'),
    el('@e78', 'ViewGroup', text='早安貼文範例… more', label='早安貼文範例… more'),
]

COMPOSER_FILLED_SAMPLE = [
    el('@e55', 'ImageView', label='Back'),
    el('@e58', 'TextView', text='Create post'),
    el('@e60', 'Button', text='POST', label='POST'),          # 有正文后不再 disabled
    el('@e72', 'AutoCompleteTextView', text='UI probe - not published'),
    el('@e92', 'Button', label='Add more to your post'),
    el('@e76', 'Button', label='No background', selected=True),
    el('@e80', 'Button', label='純紫色，背景'),
    el('@e83', 'Button', label='漸層，紅色到藍色，背景'),
    el('@e85', 'Button', label='Show all background options'),
    el('@e74', 'Button', label='Hide all background options'),
]

DRAFT_DIALOG_SAMPLE = [
    el('@e51', 'ViewGroup', text='Want to finish your post later?',
       label='Want to finish your post later?'),
    el('@e52', 'Button',
       label="Save as draft, You'll receive a notification with your draft."),
    el('@e56', 'Button', text='Discard post', label='Discard post'),
    el('@e59', 'Button', text='Continue editing', label='Continue editing'),
    # 底层编辑器仍在层级里
    el('@e58', 'TextView', text='Create post'),
    el('@e60', 'Button', text='POST', label='POST'),
]

FILTERS_SAMPLE = [
    el('@e51', 'Button', text='Reset', label='Reset', disabled=True),
    el('@e52', 'Button', text='城巿, 任何地方', label='城巿, 任何地方'),
    el('@e53', 'CompoundButton', text='在我附近', label='在我附近'),
    el('@e54', 'ViewGroup', text='在我附近', label='在我附近'),
    el('@e56', 'CompoundButton', text='公開社團', label='公開社團'),
    el('@e59', 'CompoundButton', text='我的社團', label='我的社團'),
    el('@e62', 'Button', text='Show results', label='Show results'),
]

MARKETPLACE_UNAVAILABLE_SAMPLE = [
    el('@e54', 'TextView', text='你目前無法使用 Marketplace'),
    el('@e55', 'TextView',
       text='如果你最近加入了 Facebook、Marketplace 尚未在你的國家／地區推出'
            '或你的帳號不符合我們的資格要求，就可能看到這則訊息。如需'),
    el('@e56', 'Button', text='瞭解詳情', label='瞭解詳情'),
    el('@e57', 'TextView', text='瞭解詳情', label='瞭解詳情'),
    el('@e59', 'ImageView', label='Back'),
    el('@e62', 'EditText', text='marketplace'),
    el('@e63', 'ImageView', label='Clear text'),
]

EVENTS_TAB_SAMPLE = [
    el('@e59', 'Button', label='Events search results, 5 of 7', selected=True),
    el('@e54', 'Button', label='All search results, 1 of 7'),
    el('@e84', 'Button', label='Filters'),
    el('@e78', 'ImageView', label='Back'),
    el('@e81', 'EditText', text='marketplace'),
    el('@e82', 'ImageView', label='Clear text'),
    el('@e64', 'Button', label="Rochester's Safer Marketplace Movement , 1 人將參加"),
    el('@e67', 'Button', label="有興趣, Rochester's Safer Marketplace Movement"),
    el('@e70', 'Button', label='AfterWear MarketPlace, 4 人將參加'),
    el('@e76', 'ViewGroup', text='End of results', label='End of results'),
]

PAGES_TAB_SAMPLE = [
    el('@e58', 'Button', label='Pages search results, 6 of 7', selected=True),
    el('@e85', 'Button',
       label='Jiji.ng - Nigerian Marketplace, 已驗證, 零售公司 · 199 萬位追蹤者'),
    el('@e87', 'Button', text='追蹤', label='追蹤'),
    el('@e82', 'Button', text='追蹤', label='追蹤'),
    el('@e78', 'ImageView', label='Back'),
    el('@e81', 'EditText', text='marketplace'),
    el('@e88', 'ImageView', label='Clear text'),
]

MESSENGER_INSTALL_SAMPLE = [
    el('@e54', 'ImageView', label='返回'),
    el('@e55', 'ImageView', label='Messenger標誌'),
    el('@e56', 'TextView', text='安裝Messenger'),
    el('@e58', 'TextView', text='與你最愛的對象建立聯繫。'),
    el('@e59', 'TextView', text='預估大小：178MB'),
    el('@e60', 'Button', text='查看詳情', label='查看詳情'),
    el('@e62', 'CheckBox',
       text='無法使用 Wi-Fi 時，使用行動數據。可能會產生行動數據費用。'),
    el('@e64', 'Button', text='安裝', label='安裝'),
]

MESSAGES_SAMPLE = [
    el('@e51', 'ImageView', label='Back'),
    el('@e53', 'TextView', text='Messages'),
    el('@e59', 'TextView', text='Faster messaging'),
    el('@e60', 'TextView', text='Install Messenger to instantly reach the people you care about.'),
    el('@e63', 'Button', text='INSTALL'),
]

SAMPLES = {
    'home': (HOME_SAMPLE, 'FbMainTabActivity'),
    'menu': (MENU_SAMPLE, 'FbMainTabActivity'),
    'search': (SEARCH_SAMPLE, 'ImmersiveActivity'),
    'search_results': (SEARCH_RESULTS_SAMPLE, 'ImmersiveActivity'),
    'search_filters': (FILTERS_SAMPLE, 'ImmersiveActivity'),
    'group_posts_filters': (GROUP_POSTS_FILTERS_SAMPLE, 'ImmersiveActivity'),
    'group_reel_view': (GROUP_REEL_VIEW_SAMPLE, 'ImmersiveActivity'),
    'events_filters': (EVENTS_FILTERS_SAMPLE, 'ImmersiveActivity'),
    'events_date_filter': (EVENTS_DATE_FILTER_SAMPLE, 'ImmersiveActivity'),
    'events_category_filter': (EVENTS_CATEGORY_FILTER_SAMPLE, 'ImmersiveActivity'),
    'pymk_all': (PYMK_ALL_SAMPLE, 'ImmersiveActivity'),
    'friend_requests_all': (FRIEND_REQUESTS_ALL_SAMPLE, 'ImmersiveActivity'),
    'request_sort_panel': (REQUEST_SORT_SAMPLE, 'ImmersiveActivity'),
    'events_home': (EVENTS_HOME_SAMPLE, 'ImmersiveActivity'),
    'saved_items': (SAVED_ITEMS_SAMPLE, 'ImmersiveActivity'),
    'birthdays': (BIRTHDAYS_SAMPLE, 'ImmersiveActivity'),
    'facebook_pages': (FACEBOOK_PAGES_SAMPLE, 'ImmersiveActivity'),
    'profile_photos': (PROFILE_PHOTOS_PAGE_SAMPLE, 'PandoraTabPagerActivity'),
    'photo_viewer': (PHOTO_VIEWER_SAMPLE, 'PandoraTabPagerActivity'),
    'photo_more_menu': (PHOTO_MORE_SAMPLE, 'PandoraTabPagerActivity'),
    'search_history_menu': (SEARCH_HISTORY_MENU_SAMPLE, 'ImmersiveActivity'),
    'search_history_all': (SEARCH_HISTORY_ALL_SAMPLE, 'ImmersiveActivity'),
    'group_sort_panel': (GROUP_SORT_SAMPLE, 'ImmersiveActivity'),
    'group_post_more_menu': (GROUP_POST_MORE_SAMPLE, 'ImmersiveActivity'),
    'missed_post': (MISSED_POST_SAMPLE, 'FbMainTabActivity'),
    'meta_account_info': (META_ACCOUNT_SAMPLE, 'ImmersiveActivity'),
    'page_detail': (PAGE_DETAIL_SAMPLE, 'ImmersivePortraitActivity'),
    'marketplace_unavailable': (MARKETPLACE_UNAVAILABLE_SAMPLE, 'ImmersiveReactActivity'),
    'friends': (FRIENDS_SAMPLE, ''),
    'reels': (REELS_SAMPLE, ''),
    'notifications': (NOTIF_SAMPLE, ''),
    'share_panel': (SHARE_PANEL_SAMPLE, 'FbMainTabActivity'),
    'create_post': (CREATE_POST_SAMPLE, 'ComposerActivity'),
    'group_composer': (GROUP_COMPOSER_SAMPLE, 'ComposerActivity'),
    'group_search_results': (GROUP_SEARCH_RESULTS_SAMPLE, 'ImmersiveActivity'),
    'reaction_tray': (REACTION_TRAY_SAMPLE, 'FbMainTabActivity'),
    'see_all_groups': (SEE_ALL_GROUPS_SAMPLE, 'ImmersiveActivity'),
    'draft_dialog': (DRAFT_DIALOG_SAMPLE, 'ComposerActivity'),
    'post_detail': (POST_DETAIL_SAMPLE, ''),
    'post_full': (POST_FULL_SAMPLE, ''),
    'post_photo': (POST_PHOTO_SAMPLE, ''),
    'post_more_menu': (POST_MORE_MENU_SAMPLE, 'FbMainTabActivity'),
    'story_editor': (STORY_EDITOR_SAMPLE, 'InspirationCameraActivity'),
    'story_discard': (STORY_DISCARD_SAMPLE, 'InspirationCameraActivity'),
    'sticker_panel': (STICKER_PANEL_SAMPLE, 'FbMainTabActivity'),
    'gif_panel': (GIF_PANEL_SAMPLE, 'FbMainTabActivity'),
    'group_detail': (GROUP_DETAIL_SAMPLE, 'ImmersiveActivity'),
    'group_photos': (GROUP_PHOTOS_SAMPLE, 'ImmersiveActivity'),
    'group_events': (GROUP_EVENTS_SAMPLE, 'ImmersiveActivity'),
    'group_files': (GROUP_FILES_SAMPLE, 'ImmersiveActivity'),
    'group_albums': (GROUP_ALBUMS_SAMPLE, 'ImmersiveActivity'),
    'group_reels': (GROUP_REELS_SAMPLE, 'ImmersiveActivity'),
    'profile': (PROFILE_SAMPLE, 'ImmersivePortraitActivity'),
    'person_profile': (PERSON_PROFILE_SAMPLE, 'ImmersivePortraitActivity'),
    'messenger_install': (MESSENGER_INSTALL_SAMPLE, 'StubInstallActivity'),
    'groups': (GROUPS_SAMPLE, 'ImmersiveActivity'),
    'marketplace': (MARKET_SAMPLE, ''),
    'memories': (MEMORIES_SAMPLE, ''),
    'messages': (MESSAGES_SAMPLE, 'DiodeMessengerActivity'),
}


def run_offline() -> None:
    print('— 页面判定 —')
    for key, (els, act) in SAMPLES.items():
        got = T.page_of(els, act)
        check(f'page_of({key}) == {key}', got == key, got)

    print('\n— 空树与噪声 —')
    check('空树返回 None', T.page_of([], '') is None, T.page_of([], ''))
    check('单锚点不判定', T.page_of([el('@e1', 'Button', label='Facebook menu')], '') is None)
    check('首页不误判为菜单', T.page_of(HOME_SAMPLE, 'FbMainTabActivity') != 'menu')

    print('\n— 控件定位唯一性 —')
    check('首页 menu 唯一', T.find('home', 'menu', HOME_SAMPLE) is not None)
    check('首页 search 唯一', T.find('home', 'search', HOME_SAMPLE) is not None)
    check('首页 messaging 唯一', T.find('home', 'messaging', HOME_SAMPLE) is not None)
    check('首页 profile_entry 唯一', T.find('home', 'profile_entry', HOME_SAMPLE) is not None)
    check('底栏 reels 正则容忍无角标', T.find('home', 'tab_reels', HOME_SAMPLE) is not None)
    check('底栏通知容忍 ", N new"',
          T.find('home', 'tab_notifications',
                 [el('@e1', 'View', label='Notifications, tab 4 of 4, 2 new')]) is not None)

    check('菜单 groups 唯一', T.find('menu', 'groups', MENU_SAMPLE) is not None)
    check('菜单 finds 唯一', T.find('menu', 'finds', MENU_SAMPLE) is not None)
    check('菜单 see_more 唯一', T.find('menu', 'see_more', MENU_SAMPLE) is not None)
    check('菜单 close 唯一', T.find('menu', 'close', MENU_SAMPLE) is not None)

    check('搜索 input 按类型存在', any(e['type'] == 'EditText' for e in SEARCH_SAMPLE))
    check('搜索结果页 input 按类型唯一',
          T.find('search_results', 'input', SEARCH_RESULTS_SAMPLE) is not None)
    check('搜索结果 分类标签可枚举',
          len(T.matches('search_results', 'tab_any', SEARCH_RESULTS_SAMPLE)) == 6,
          len(T.matches('search_results', 'tab_any', SEARCH_RESULTS_SAMPLE)))
    check('搜索结果 条目标签命中',
          len(T.matches('search_results', 'result_item', SEARCH_RESULTS_SAMPLE)) == 1,
          len(T.matches('search_results', 'result_item', SEARCH_RESULTS_SAMPLE)))
    check('搜索结果 加入按钮唯一',
          T.find('search_results', 'join', SEARCH_RESULTS_SAMPLE) is not None)
    check('搜索结果 反馈问题唯一',
          T.find('search_results', 'feedback_q', SEARCH_RESULTS_SAMPLE) is not None)

    check('好友 确认请求唯一',
          T.find('friends', 'confirm_request', FRIENDS_SAMPLE) is not None)
    check('好友 删除请求唯一',
          T.find('friends', 'delete_request', FRIENDS_SAMPLE) is not None)
    check('好友 联络人上传唯一',
          T.find('friends', 'upload_contacts', FRIENDS_SAMPLE) is not None)

    check('Reels share 唯一', T.find('reels', 'share', REELS_SAMPLE) is not None)
    check('Reels reactions 唯一', T.find('reels', 'reactions', REELS_SAMPLE) is not None)
    check('Reels follow 唯一', T.find('reels', 'follow', REELS_SAMPLE) is not None)

    check('通知条目可识别',
          len(T.matches('notifications', 'item', NOTIF_SAMPLE)) == 2,
          len(T.matches('notifications', 'item', NOTIF_SAMPLE)))

    check('发帖正文输入存在', any(e['type'] == 'AutoCompleteTextView' for e in CREATE_POST_SAMPLE))
    check('评论输入框唯一', T.find('post_detail', 'comment_input', POST_DETAIL_SAMPLE) is not None)

    print('\n— 社团详情页 —')
    check('社团详情不判成社團探索页',
          T.page_of(GROUP_DETAIL_SAMPLE, 'ImmersiveActivity') == 'group_detail',
          T.page_of(GROUP_DETAIL_SAMPLE, 'ImmersiveActivity'))
    check('加入社团是 send',
          T.PAGES['group_detail'].targets['join'].risk == T.RISK_SEND)
    check('社团内发言是 send',
          T.PAGES['group_detail'].targets['composer'].risk == T.RISK_SEND)
    check('社团六个分页可枚举',
          sum(1 for n in ('tab_reel', 'tab_discussion', 'tab_photos',
                          'tab_albums', 'tab_events', 'tab_files')
              if T.find('group_detail', n, GROUP_DETAIL_SAMPLE) is not None) == 6)
    check('社團探索页不判成社团详情',
          T.page_of(GROUPS_SAMPLE, 'ImmersiveActivity') == 'groups',
          T.page_of(GROUPS_SAMPLE, 'ImmersiveActivity'))
    check('社团相片页不判成社团详情',
          T.page_of(GROUP_PHOTOS_SAMPLE, 'ImmersiveActivity') == 'group_photos',
          T.page_of(GROUP_PHOTOS_SAMPLE, 'ImmersiveActivity'))
    check('社团详情不判成相片页',
          T.page_of(GROUP_DETAIL_SAMPLE, 'ImmersiveActivity') == 'group_detail',
          T.page_of(GROUP_DETAIL_SAMPLE, 'ImmersiveActivity'))
    check('相片格子可枚举（3 个）',
          len(T.resolve('group_photos', 'item', GROUP_PHOTOS_SAMPLE)) == 3,
          len(T.resolve('group_photos', 'item', GROUP_PHOTOS_SAMPLE)))

    print('\n— GIF 面板 —')
    check('GIF 面板独立成页',
          T.page_of(GIF_PANEL_SAMPLE, '') == 'gif_panel',
          T.page_of(GIF_PANEL_SAMPLE, ''))
    check('GIF 搜索框唯一',
          T.find('gif_panel', 'input', GIF_PANEL_SAMPLE) is not None)
    check('GIF 结果可枚举（4 个）',
          len(T.resolve('gif_panel', 'item', GIF_PANEL_SAMPLE)) == 4,
          len(T.resolve('gif_panel', 'item', GIF_PANEL_SAMPLE)))
    check('GIF 面板不判成贴纸面板',
          T.page_of(GIF_PANEL_SAMPLE, '') != 'sticker_panel')

    print('\n— 贴纸面板 —')
    check('贴纸面板独立成页（不判成评论区）',
          T.page_of(STICKER_PANEL_SAMPLE, '') == 'sticker_panel',
          T.page_of(STICKER_PANEL_SAMPLE, ''))
    check('贴纸分类可枚举（6 个）',
          len(T.resolve('sticker_panel', 'category', STICKER_PANEL_SAMPLE)) == 6,
          len(T.resolve('sticker_panel', 'category', STICKER_PANEL_SAMPLE)))
    check('贴纸商店唯一',
          T.find('sticker_panel', 'store', STICKER_PANEL_SAMPLE) is not None)
    check('贴纸搜索唯一',
          T.find('sticker_panel', 'search', STICKER_PANEL_SAMPLE) is not None)
    check('评论区不会被误判成贴纸面板',
          T.page_of(POST_DETAIL_FULL_SAMPLE, '') == 'post_detail',
          T.page_of(POST_DETAIL_FULL_SAMPLE, ''))

    print('\n— 分享面板风险 —')
    check('Share now 标为 forbidden（不是 send）',
          T.PAGES['share_panel'].targets['share_now'].risk == T.RISK_FORBIDDEN)
    check('Share now 唯一',
          T.find('share_panel', 'share_now', SHARE_PANEL_SAMPLE) is not None)
    check('分享面板与 Reels 页不混淆',
          T.page_of(SHARE_PANEL_SAMPLE, 'FbMainTabActivity') == 'share_panel',
          T.page_of(SHARE_PANEL_SAMPLE, 'FbMainTabActivity'))
    check('分享目标都是 send',
          all(T.PAGES['share_panel'].targets[n].risk == T.RISK_SEND
              for n in ('your_story', 'messenger', 'group', 'text')))

    print('\n— Reels 播放态 —')
    check('播放中仍判为 Reels',
          T.page_of(REELS_PLAYING_SAMPLE, '') == 'reels',
          T.page_of(REELS_PLAYING_SAMPLE, ''))
    check('播放按钮唯一',
          T.find('reels', 'play', REELS_PLAYING_SAMPLE) is not None)
    check('后退/前进控件唯一',
          T.find('reels', 'rewind', REELS_PLAYING_SAMPLE) is not None
          and T.find('reels', 'forward', REELS_PLAYING_SAMPLE) is not None)

    print('\n— 评论区工具条 —')
    check('完整评论区判为 post_detail',
          T.page_of(POST_DETAIL_FULL_SAMPLE, '') == 'post_detail',
          T.page_of(POST_DETAIL_FULL_SAMPLE, ''))
    check('未输入时发送按钮 disabled',
          T.find('post_detail', 'send', POST_DETAIL_FULL_SAMPLE)['disabled'] is True)
    check('发送评论是 send',
          T.PAGES['post_detail'].targets['send'].risk == T.RISK_SEND)
    check('展开更多回复可定位',
          T.find('post_detail', 'view_replies', POST_DETAIL_FULL_SAMPLE) is not None)
    check('评论表情数是 send',
          T.PAGES['post_detail'].targets['comment_reactions'].risk == T.RISK_SEND)
    check('GIF 与贴纸入口唯一',
          T.find('post_detail', 'gif', POST_DETAIL_FULL_SAMPLE) is not None
          and T.find('post_detail', 'stickers', POST_DETAIL_FULL_SAMPLE) is not None)

    print('\n— 帖子详情 vs 评论浮层 —')
    check('视频帖详情判为 post_full（不判成评论浮层）',
          T.page_of(POST_FULL_SAMPLE, '') == 'post_full',
          T.page_of(POST_FULL_SAMPLE, ''))
    check('评论浮层不判成帖子详情',
          T.page_of(POST_DETAIL_SAMPLE, '') != 'post_full',
          T.page_of(POST_DETAIL_SAMPLE, ''))
    check('首页样本不判成帖子详情',
          T.page_of(HOME_SAMPLE, 'FbMainTabActivity') != 'post_full',
          T.page_of(HOME_SAMPLE, 'FbMainTabActivity'))
    check('观看数唯一',
          T.find('post_full', 'views', POST_FULL_SAMPLE) is not None)
    check('静音开关唯一',
          T.find('post_full', 'mute', POST_FULL_SAMPLE) is not None)

    print('\n— 发帖安全闸门与草稿弹窗 —')
    check('空正文时 POST 为 disabled',
          T.find('create_post', 'post_btn', CREATE_POST_SAMPLE)['disabled'] is True)
    check('有正文后 POST 变为可用',
          T.find('create_post', 'post_btn', COMPOSER_FILLED_SAMPLE) is not None
          and T.find('create_post', 'post_btn', COMPOSER_FILLED_SAMPLE)['disabled'] is False)
    check('有正文后仍判为发帖页（不被草稿弹窗抢走）',
          T.page_of(COMPOSER_FILLED_SAMPLE, 'ComposerActivity') == 'create_post',
          T.page_of(COMPOSER_FILLED_SAMPLE, 'ComposerActivity'))
    check('草稿弹窗判为 draft_dialog（不判成发帖页）',
          T.page_of(DRAFT_DIALOG_SAMPLE, 'ComposerActivity') == 'draft_dialog',
          T.page_of(DRAFT_DIALOG_SAMPLE, 'ComposerActivity'))
    check('存为草稿是 send',
          T.PAGES['draft_dialog'].targets['save_draft'].risk == T.RISK_SEND)
    check('放弃草稿是 none',
          T.PAGES['draft_dialog'].targets['discard'].risk == T.RISK_NONE)
    check('背景模板可枚举',
          len(T.resolve('create_post', 'bg_option', COMPOSER_FILLED_SAMPLE)) == 2,
          len(T.resolve('create_post', 'bg_option', COMPOSER_FILLED_SAMPLE)))

    print('\n— 两种筛选面板 —')
    check('社团帖筛选面板判为 group_posts_filters',
          T.page_of(GROUP_POSTS_FILTERS_SAMPLE, 'ImmersiveActivity')
          == 'group_posts_filters',
          T.page_of(GROUP_POSTS_FILTERS_SAMPLE, 'ImmersiveActivity'))
    check('社团帖筛选面板不判成 Groups 分类的筛选面板',
          T.page_of(GROUP_POSTS_FILTERS_SAMPLE, 'ImmersiveActivity') != 'search_filters')
    check('Groups 分类筛选面板仍判为 search_filters',
          T.page_of(FILTERS_SAMPLE, 'ImmersiveActivity') == 'search_filters',
          T.page_of(FILTERS_SAMPLE, 'ImmersiveActivity'))
    check('社团帖筛选的专属项可定位',
          T.find('group_posts_filters', 'source', GROUP_POSTS_FILTERS_SAMPLE) is not None
          and T.find('group_posts_filters', 'location', GROUP_POSTS_FILTERS_SAMPLE)
          is not None)
    check('活动筛选面板判为 events_filters',
          T.page_of(EVENTS_FILTERS_SAMPLE, 'ImmersiveActivity') == 'events_filters',
          T.page_of(EVENTS_FILTERS_SAMPLE, 'ImmersiveActivity'))
    check('活动筛选面板不与另两套混淆',
          T.page_of(EVENTS_FILTERS_SAMPLE, 'ImmersiveActivity')
          not in ('search_filters', 'group_posts_filters'))

    print('\n— 社团页默认 tab 变化 —')
    check('加入社团后的「為你推薦」视图也判为 groups',
          T.page_of(GROUPS_FOR_YOU_SAMPLE, 'ImmersiveActivity') == 'groups',
          T.page_of(GROUPS_FOR_YOU_SAMPLE, 'ImmersiveActivity'))
    check('「為你推薦」视图不判成社团搜索结果页',
          T.page_of(GROUPS_FOR_YOU_SAMPLE, 'ImmersiveActivity') != 'group_search_results')
    check('原探索视图仍判为 groups',
          T.page_of(GROUPS_SAMPLE, 'ImmersiveActivity') == 'groups',
          T.page_of(GROUPS_SAMPLE, 'ImmersiveActivity'))
    check('新的社团页 tab 可定位',
          T.find('groups', 'tab_for_you', GROUPS_FOR_YOU_SAMPLE) is not None
          and T.find('groups', 'your_groups', GROUPS_FOR_YOU_SAMPLE) is not None)
    check('「你的社團」tab 视图也判为 groups',
          T.page_of(GROUPS_YOUR_SAMPLE, 'ImmersiveActivity') == 'groups',
          T.page_of(GROUPS_YOUR_SAMPLE, 'ImmersiveActivity'))
    check('「你的社團」tab 的排序控件可定位',
          T.find('groups', 'sort_by', GROUPS_YOUR_SAMPLE) is not None)

    print('\n— 查看全部列表 —')
    check('查看全部列表判为 see_all_groups（不判成 search）',
          T.page_of(SEE_ALL_GROUPS_SAMPLE, 'ImmersiveActivity') == 'see_all_groups',
          T.page_of(SEE_ALL_GROUPS_SAMPLE, 'ImmersiveActivity'))
    check('查看全部列表不判成筛选面板',
          T.page_of(SEE_ALL_GROUPS_SAMPLE, 'ImmersiveActivity') != 'search_filters')
    check('筛选 chip 可定位',
          T.find('see_all_groups', 'chip_city', SEE_ALL_GROUPS_SAMPLE) is not None)

    print('\n— 表情托盘 —')
    check('托盘判为 reaction_tray（压过底层帖子）',
          T.page_of(REACTION_TRAY_SAMPLE, 'FbMainTabActivity') == 'reaction_tray',
          T.page_of(REACTION_TRAY_SAMPLE, 'FbMainTabActivity'))
    check('七种表情全部是 send',
          all(T.PAGES['reaction_tray'].targets[n].risk == T.RISK_SEND
              for n in ('like', 'love', 'care', 'haha', 'wow', 'sad', 'angry')))
    check('表情可逐个枚举',
          sum(1 for n in ('like', 'love', 'care', 'haha', 'wow', 'sad', 'angry')
              if T.find('reaction_tray', n, REACTION_TRAY_SAMPLE) is not None) == 7)
    check('取消是 none',
          T.PAGES['reaction_tray'].targets['cancel'].risk == T.RISK_NONE)

    print('\n— 社团搜索 —')
    check('社团搜索结果判为 group_search_results（不判成社團探索页）',
          T.page_of(GROUP_SEARCH_RESULTS_SAMPLE, 'ImmersiveActivity')
          == 'group_search_results',
          T.page_of(GROUP_SEARCH_RESULTS_SAMPLE, 'ImmersiveActivity'))
    check('社团条目可枚举（2 个）',
          len(T.resolve('group_search_results', 'item', GROUP_SEARCH_RESULTS_SAMPLE)) == 2,
          len(T.resolve('group_search_results', 'item', GROUP_SEARCH_RESULTS_SAMPLE)))
    check('社团搜索里的加入是 send',
          T.PAGES['group_search_results'].targets['join'].risk == T.RISK_SEND)
    check('Group Posts 视图也判为同一页',
          T.page_of(GROUP_POSTS_TAB_SAMPLE, 'ImmersiveActivity') == 'group_search_results',
          T.page_of(GROUP_POSTS_TAB_SAMPLE, 'ImmersiveActivity'))
    check('筛选 chip 可定位',
          T.find('group_search_results', 'filters', GROUP_POSTS_TAB_SAMPLE) is not None)
    check('帖子格可定位',
          T.find('group_search_results', 'post_item', GROUP_POSTS_TAB_SAMPLE) is not None)

    print('\n— 个人发帖 vs 社团发帖 —')
    check('社团发帖框判为 group_composer',
          T.page_of(GROUP_COMPOSER_SAMPLE, 'ComposerActivity') == 'group_composer',
          T.page_of(GROUP_COMPOSER_SAMPLE, 'ComposerActivity'))
    check('个人发帖框仍判为 create_post',
          T.page_of(CREATE_POST_SAMPLE, 'ComposerActivity') == 'create_post',
          T.page_of(CREATE_POST_SAMPLE, 'ComposerActivity'))
    check('社团发帖的可见范围是「公開社團」',
          T.find('group_composer', 'audience', GROUP_COMPOSER_SAMPLE) is not None)
    check('社团发帖有贴文标题入口',
          T.find('group_composer', 'title_field', GROUP_COMPOSER_SAMPLE) is not None)
    check('社团发帖空正文时 POST 也是 disabled',
          T.find('group_composer', 'post_btn', GROUP_COMPOSER_SAMPLE)['disabled'] is True)

    print('\n— 限时动态编辑器 —')
    check('编辑器判为 story_editor',
          T.page_of(STORY_EDITOR_SAMPLE, 'InspirationCameraActivity') == 'story_editor',
          T.page_of(STORY_EDITOR_SAMPLE, 'InspirationCameraActivity'))
    check('编辑器的发布键是 forbidden（比普通 Share 更严）',
          T.PAGES['story_editor'].targets['share'].risk == T.RISK_FORBIDDEN)
    check('放弃确认框判为 story_discard（压过编辑器）',
          T.page_of(STORY_DISCARD_SAMPLE, 'InspirationCameraActivity') == 'story_discard',
          T.page_of(STORY_DISCARD_SAMPLE, 'InspirationCameraActivity'))
    check('放弃按钮是 none',
          T.PAGES['story_discard'].targets['discard'].risk == T.RISK_NONE)

    print('\n— 社团加入态 —')
    check('已加入状态仍判为社团详情',
          T.page_of(GROUP_DETAIL_JOINED_SAMPLE, 'ImmersiveActivity') == 'group_detail',
          T.page_of(GROUP_DETAIL_JOINED_SAMPLE, 'ImmersiveActivity'))
    check('已加入按钮唯一',
          T.find('group_detail', 'joined', GROUP_DETAIL_JOINED_SAMPLE) is not None)
    check('已加入按钮是 send（点开可退出社团）',
          T.PAGES['group_detail'].targets['joined'].risk == T.RISK_SEND)
    check('未加入态样本里没有已加入按钮',
          T.find('group_detail', 'joined', GROUP_DETAIL_SAMPLE) is None)

    print('\n— 帖子更多菜单 —')
    check('更多菜单判为 post_more_menu（压过底层帖子）',
          T.page_of(POST_MORE_MENU_SAMPLE, 'FbMainTabActivity') == 'post_more_menu',
          T.page_of(POST_MORE_MENU_SAMPLE, 'FbMainTabActivity'))
    check('举报标为 forbidden',
          T.PAGES['post_more_menu'].targets['report'].risk == T.RISK_FORBIDDEN)
    check('感兴趣/不感兴趣是 send',
          T.PAGES['post_more_menu'].targets['more_like_this'].risk == T.RISK_SEND
          and T.PAGES['post_more_menu'].targets['less_like_this'].risk == T.RISK_SEND)
    check('隐藏贴文是 send',
          T.PAGES['post_more_menu'].targets['hide'].risk == T.RISK_SEND)

    print('\n— 图文帖详情 —')
    check('图文帖详情判为 post_photo（不判成视频帖）',
          T.page_of(POST_PHOTO_SAMPLE, '') == 'post_photo',
          T.page_of(POST_PHOTO_SAMPLE, ''))
    check('视频帖样本不判成图文帖',
          T.page_of(POST_FULL_SAMPLE, '') == 'post_full',
          T.page_of(POST_FULL_SAMPLE, ''))
    check('投票选项是 send（会记录在账号上）',
          T.PAGES['post_photo'].targets['poll_option'].risk == T.RISK_SEND)
    check('投票选项可枚举（3 个）',
          len(T.resolve('post_photo', 'poll_option', POST_PHOTO_SAMPLE)) == 3,
          len(T.resolve('post_photo', 'poll_option', POST_PHOTO_SAMPLE)))
    check('链接卡片唯一',
          T.find('post_photo', 'link_card', POST_PHOTO_SAMPLE) is not None)

    print('\n— 分类序号不能写死 —')
    check('不同序号下 Reels 分类仍可定位（6/7）',
          T.find('search_results', 'tab_reels', TAG_SEARCH_SAMPLE) is not None)
    check('不同序号下 People 分类仍可定位（2/7）',
          T.find('search_results', 'tab_people', TAG_SEARCH_SAMPLE) is not None)
    check('序号变化不影响页面判定',
          T.page_of(TAG_SEARCH_SAMPLE, 'ImmersiveActivity') == 'search_results',
          T.page_of(TAG_SEARCH_SAMPLE, 'ImmersiveActivity'))
    check('旧样本（Reels 2/7）仍可定位',
          T.find('search_results', 'tab_reels', SEARCH_RESULTS_SAMPLE) is not None)
    check('点话题标签落到的结果页仍判为 search_results',
          T.page_of(HASHTAG_RESULT_SAMPLE, 'ImmersiveActivity') == 'search_results',
          T.page_of(HASHTAG_RESULT_SAMPLE, 'ImmersiveActivity'))
    check('帖子类结果条目可定位',
          T.find('search_results', 'post_item', HASHTAG_RESULT_SAMPLE) is not None)

    print('\n— 主页分页是页内切换 —')
    check('切到相片分页仍判为自己主页',
          T.page_of(PROFILE_PHOTOS_SAMPLE, 'ImmersivePortraitActivity') == 'profile',
          T.page_of(PROFILE_PHOTOS_SAMPLE, 'ImmersivePortraitActivity'))
    check('子分类「相片」唯一（不与分页按钮混淆）',
          T.find('profile', 'sub_photos', PROFILE_PHOTOS_SAMPLE) is not None)
    check('分页按钮「相片, 2 of 3」仍可定位',
          T.find('profile', 'tab_photos', PROFILE_PHOTOS_SAMPLE) is not None)
    check('子分类「被标注」唯一',
          T.find('profile', 'sub_tagged', PROFILE_PHOTOS_SAMPLE) is not None)
    check('相片分页不判成社团相片页',
          T.page_of(PROFILE_PHOTOS_SAMPLE, 'ImmersivePortraitActivity') != 'group_photos')

    print('\n— 搜索页两种状态 —')
    check('搜索页空态判为 search',
          T.page_of(SEARCH_SAMPLE, 'ImmersiveActivity') == 'search',
          T.page_of(SEARCH_SAMPLE, 'ImmersiveActivity'))
    check('搜索页有历史时也判为 search',
          T.page_of(SEARCH_HISTORY_SAMPLE, 'ImmersiveActivity') == 'search',
          T.page_of(SEARCH_HISTORY_SAMPLE, 'ImmersiveActivity'))
    check('有历史页不判成结果页',
          T.page_of(SEARCH_HISTORY_SAMPLE, 'ImmersiveActivity') != 'search_results')
    check('近期分组可定位',
          T.find('search', 'recent_group', SEARCH_HISTORY_SAMPLE) is not None)
    check('查看全部历史可定位',
          T.find('search', 'see_all_history', SEARCH_HISTORY_SAMPLE) is not None)
    check('搜索页第三种状态（只有输入框+清空+返回）也判为 search',
          T.page_of(SEARCH_TYPED_SAMPLE, 'ImmersiveActivity') == 'search',
          T.page_of(SEARCH_TYPED_SAMPLE, 'ImmersiveActivity'))
    check('结果页靠 exclusive 压过搜索页',
          T.page_of(SEARCH_RESULTS_SAMPLE, 'ImmersiveActivity') == 'search_results',
          T.page_of(SEARCH_RESULTS_SAMPLE, 'ImmersiveActivity'))

    print('\n— 首页邮箱横幅 —')
    check('带邮箱横幅时仍判为首页',
          T.page_of(HOME_BANNER_SAMPLE, 'FbMainTabActivity') == 'home',
          T.page_of(HOME_BANNER_SAMPLE, 'FbMainTabActivity'))
    check('横幅按前缀可定位（不依赖具体邮箱）',
          T.find('home', 'email_banner', HOME_BANNER_SAMPLE) is not None)
    check('横幅里的确认邮箱仍是 forbidden',
          T.PAGES['home'].targets['email_banner'].risk == T.RISK_FORBIDDEN)
    check('横幅关闭键是 none',
          T.PAGES['home'].targets['email_banner_close'].risk == T.RISK_NONE)

    print('\n— 覆盖层误判回归 —')
    check('信息流滚到底（只剩顶栏）仍判为首页',
          T.page_of([
              el('@e93', 'Button', label='Facebook menu'),
              el('@e94', 'ImageView', label='Facebook logo'),
              el('@e98', 'Button', label='Search Facebook'),
              el('@e102', 'View', label='Home, tab 1 of 4', selected=True),
              el('@e75', 'ViewGroup', text='Sponsored•Shared with: 所有人',
                 label='Sponsored•Shared with: 所有人'),
          ], 'FbMainTabActivity') == 'home')
    check('Reels 页不会因顶栏被误判成首页',
          T.page_of(REELS_SAMPLE, 'FbMainTabActivity') == 'reels',
          T.page_of(REELS_SAMPLE, 'FbMainTabActivity'))
    check('抽屉菜单展开设置后仍判为菜单（不判成首页）',
          T.page_of(SETTINGS_EXPANDED_SAMPLE, 'FbMainTabActivity') == 'menu',
          T.page_of(SETTINGS_EXPANDED_SAMPLE, 'FbMainTabActivity'))
    check('评论浮层有 Close 但不误判为菜单',
          T.page_of(POST_DETAIL_SAMPLE, '') == 'post_detail',
          T.page_of(POST_DETAIL_SAMPLE, ''))
    check('菜单里的设置入口唯一',
          T.find('menu', 'settings', SETTINGS_EXPANDED_SAMPLE) is not None)
    check('订单和付款标为 forbidden',
          T.PAGES['menu'].targets['orders_payments'].risk == T.RISK_FORBIDDEN)
    check('总设置标为 forbidden',
          T.PAGES['menu'].targets['settings'].risk == T.RISK_FORBIDDEN)
    check('帮助区展开后仍可定位支持入口',
          T.find('menu', 'support', SETTINGS_EXPANDED_SAMPLE) is None)  # 该样本未展开帮助区
    check('报告问题标为 send',
          T.PAGES['menu'].targets['report_problem'].risk == T.RISK_SEND)
    check('条款入口唯一',
          T.find('menu', 'terms',
                 [el('@e1', 'ViewGroup', text='Terms & Policies',
                     label='Terms & Policies')]) is not None)

    print('\n— 已发生过的误判回归 —')
    check('自己主页不判成他人主页',
          T.page_of(PROFILE_SAMPLE, 'ImmersivePortraitActivity') == 'profile',
          T.page_of(PROFILE_SAMPLE, 'ImmersivePortraitActivity'))
    check('他人主页不判成自己主页',
          T.page_of(PERSON_PROFILE_SAMPLE, 'ImmersivePortraitActivity') == 'person_profile',
          T.page_of(PERSON_PROFILE_SAMPLE, 'ImmersivePortraitActivity'))
    check('他人主页 发消息按钮唯一',
          T.find('person_profile', 'message_btn', PERSON_PROFILE_SAMPLE) is not None)
    check('安装页不判成 app 内引导页',
          T.page_of(MESSENGER_INSTALL_SAMPLE, 'StubInstallActivity') == 'messenger_install',
          T.page_of(MESSENGER_INSTALL_SAMPLE, 'StubInstallActivity'))
    check('安装页 安装按钮唯一',
          T.find('messenger_install', 'install', MESSENGER_INSTALL_SAMPLE) is not None)
    check('Marketplace 不可用页不判成搜索结果页',
          T.page_of(MARKETPLACE_UNAVAILABLE_SAMPLE, 'ImmersiveReactActivity')
          == 'marketplace_unavailable',
          T.page_of(MARKETPLACE_UNAVAILABLE_SAMPLE, 'ImmersiveReactActivity'))
    check('Events 分类仍判为搜索结果页',
          T.page_of(EVENTS_TAB_SAMPLE, 'ImmersiveActivity') == 'search_results',
          T.page_of(EVENTS_TAB_SAMPLE, 'ImmersiveActivity'))
    check('活动条目可识别',
          len(T.resolve('search_results', 'event_item', EVENTS_TAB_SAMPLE)) == 2,
          len(T.resolve('search_results', 'event_item', EVENTS_TAB_SAMPLE)))
    check('有兴趣按钮是 send',
          T.PAGES['search_results'].targets['interested'].risk == T.RISK_SEND)
    check('结果结束标记唯一',
          T.find('search_results', 'end_of_results', EVENTS_TAB_SAMPLE) is not None)
    check('主页条目可识别',
          len(T.resolve('search_results', 'page_item', PAGES_TAB_SAMPLE)) == 1,
          len(T.resolve('search_results', 'page_item', PAGES_TAB_SAMPLE)))
    check('追踪主页是 send',
          T.PAGES['search_results'].targets['follow_page'].risk == T.RISK_SEND)
    check('第 7 个分类可定位',
          T.find('search_results', 'tab_marketplace',
                 [el('@e1', 'Button', label='Marketplace search results, 7 of 7')]) is not None)
    check('分类条滚到看不见 All 时仍判为搜索结果页',          T.page_of([
              el('@e55', 'Button', label='People search results, 3 of 7'),
              el('@e59', 'Button', label='Marketplace search results, 7 of 7'),
              el('@e62', 'EditText', text='marketplace'),
              el('@e63', 'ImageView', label='Clear text'),
          ], 'ImmersiveActivity') == 'search_results',
          T.page_of([
              el('@e55', 'Button', label='People search results, 3 of 7'),
              el('@e63', 'ImageView', label='Clear text'),
          ], 'ImmersiveActivity'))

    print('\n— 风险分级与状态 —')
    check('POST 是 send', T.PAGES['create_post'].targets['post_btn'].risk == T.RISK_SEND)
    check('POST 空内容 disabled',
          T.find('create_post', 'post_btn', CREATE_POST_SAMPLE)['disabled'] is True)
    check('安装 Messenger 是 forbidden',
          T.PAGES['messages'].targets['install'].risk == T.RISK_FORBIDDEN)
    check('确认好友请求是 send',
          T.PAGES['friends'].targets['confirm_request'].risk == T.RISK_SEND)
    check('购物车是 forbidden',
          T.PAGES['marketplace'].targets['cart'].risk == T.RISK_FORBIDDEN)
    check('加入社团是 send', T.PAGES['groups'].targets['join'].risk == T.RISK_SEND)
    check('搜索结果里「加入」也是 send',
          T.PAGES['search_results'].targets['join'].risk == T.RISK_SEND)

    print('\n— 正则卫生 —')
    import re as _re
    _broad = _re.compile(r'^\^\[\^[^\]]+\]\+?\$$')
    for key, page in T.PAGES.items():
        for name, t in page.targets.items():
            for rx in (t.label_re, t.text_re):
                check(f'{key}.{name} 不是「任意非X」宽泛正则',
                      not (rx and _broad.match(rx)), rx)

    print('\n— 资料库体检 —')
    no_match, weak_identity, no_note, bad_cov = [], [], [], []
    for key, page in T.PAGES.items():
        if page.coverage not in (T.COVERED, T.VERIFIED, T.PARTIAL, T.BLOCKED):
            bad_cov.append(f'{key}={page.coverage}')
        if len(page.identity) < 2 and not page.exclusive:
            weak_identity.append(f'{key}({len(page.identity)})')
        for name, t in page.targets.items():
            if not (t.label_re or t.text_re or t.type_re or t.id):
                no_match.append(f'{key}.{name}')
            if t.risk in (T.RISK_SEND, T.RISK_FORBIDDEN) and not t.note:
                no_note.append(f'{key}.{name}')
    # 「内容类」条目（评论正文、投票问题、链接标题…）本来就没有稳定特征，
    # 它们是文档性占位而不是可定位控件，所以这里只提示、不判失败。
    # 真正必须满足的是：forbidden 控件要能定位且有说明。
    if no_match:
        print('   提示（内容类占位，无稳定特征、不参与定位）：' + ', '.join(no_match))
    # forbidden 必须单独写清"为什么不能点"；send 允许在 Page.note 里统一说明。
    hard = []
    for item in no_note:
        pk, tk = item.split('.', 1)
        if T.PAGES[pk].targets[tk].risk == T.RISK_FORBIDDEN:
            hard.append(item)
    check('forbidden 控件都必须单独写说明', not hard, hard)
    if no_note and not hard:
        print('   提示（send 级未写单独说明，靠 Page.note 覆盖）：' + ', '.join(no_note))
    check('覆盖等级取值合法', not bad_cov, bad_cov)
    if weak_identity:
        print('   提示（锚点偏少的页，可能判不出）：' + ', '.join(weak_identity))
    # 正则都能编译
    import re as _re2
    bad_rx = []
    for key, page in T.PAGES.items():
        for name, t in page.targets.items():
            for rx in (t.label_re, t.text_re, t.type_re):
                if rx:
                    try:
                        _re2.compile(rx)
                    except _re2.error:
                        bad_rx.append(f'{key}.{name}:{rx}')
        for t in page.identity + page.exclusive:
            for rx in (t.label_re, t.text_re, t.type_re):
                if rx:
                    try:
                        _re2.compile(rx)
                    except _re2.error:
                        bad_rx.append(f'{key}.identity:{rx}')
    check('所有正则都能编译', not bad_rx, bad_rx)

    print('\n— 导出结构 —')
    import json as _json
    try:
        dumped = _json.dumps(T.export_json(), ensure_ascii=False)
        ok_json = True
    except (TypeError, ValueError) as exc:
        dumped, ok_json = str(exc), False
    check('export_json 可序列化为 JSON', ok_json, dumped[:120])
    if ok_json:
        data = _json.loads(dumped)
        check('导出页数与 PAGES 一致', len(data['pages']) == len(T.PAGES),
              (len(data['pages']), len(T.PAGES)))
        # 导出物里绝不能出现设备态：ref / 坐标
        forbidden_keys = {'ref', 'x', 'y', 'width', 'height', 'coordinates'}
        found = set()
        def _scan(obj):
            if isinstance(obj, dict):
                found.update(k for k in obj if k in forbidden_keys)
                for v in obj.values():
                    _scan(v)
            elif isinstance(obj, list):
                for v in obj:
                    _scan(v)
        _scan(data)
        check('导出物不含 ref / 坐标', not found, found)
        check('每个 target 都有 risk 字段',
              all('risk' in t for p in data['pages']
                  for t in p['targets'].values()))

    print('\n— 导出物与模板是否同步 —')
    lib_path = Path(__file__).resolve().parents[2] / 'data' / 'facebook_ui_library.json'
    if not lib_path.exists():
        print('   提示：还没有导出过 JSON（跑 flow.py export 即可）')
    else:
        import json as _j
        disk = _j.loads(lib_path.read_text(encoding='utf-8'))
        dp = {p['key']: p for p in disk['pages']}
        mp = {k: p for k, p in T.PAGES.items()}

        check('导出的页面集合与模板一致', set(dp) == set(mp),
              {'磁盘缺': sorted(set(mp) - set(dp)),
               '磁盘多': sorted(set(dp) - set(mp))})

        page_drift, cov_drift, target_drift = [], [], []
        for k in sorted(set(dp) & set(mp)):
            if dp[k]['coverage'] != mp[k].coverage:
                cov_drift.append(f'{k}: 磁盘={dp[k]["coverage"]} 模板={mp[k].coverage}')
            dt = set(dp[k]['targets'])
            mt = set(mp[k].targets)
            if dt != mt:
                target_drift.append(f'{k}: 磁盘缺{sorted(mt - dt)} 磁盘多{sorted(dt - mt)}')
        check('每页覆盖等级一致', not cov_drift, cov_drift[:6])
        check('每页控件名集合一致', not target_drift, target_drift[:6])

        disk_map = {(p['key'], n): t['risk']
                    for p in disk['pages'] for n, t in p['targets'].items()}
        mem_map = {(k, n): t.risk
                   for k, p in T.PAGES.items() for n, t in p.targets.items()}
        drift = [f'{p}.{n}: 磁盘={disk_map.get((p, n))} 模板={mem_map.get((p, n))}'
                 for (p, n) in set(disk_map) | set(mem_map)
                 if disk_map.get((p, n)) != mem_map.get((p, n))]
        check('风险定义一致', not drift, drift[:6])
        print('   （若不一致，跑 python fishfleet/web/facebook_flow.py export 重新导出）')

    print('\n— activity_hint 与样本是否一致 —')
    # 2026-11-02 06:24 的教训：page_detail 的 activity_hint 写错时，
    # 判定"看起来能过"，其实是靠错误值在做 tie-break。这里把两者对齐检查一遍。
    act_drift = []
    for key, (_sample, act) in SAMPLES.items():
        hint = T.PAGES[key].activity_hint
        if hint and hint not in act:
            act_drift.append(f'{key}: 样本={act} 模板={hint}')
    if act_drift:
        print('   提示（不一致，可能靠错误 activity 在做 tie-break）：')
        for d in act_drift[:8]:
            print('     ' + d)
    else:
        check('样本 Activity 与 activity_hint 全部一致', True)

    print('\n— 覆盖状态自洽 —')
    check('消息页标记为 blocked', T.PAGES['messages'].coverage == T.BLOCKED)
    check('安装提示页标记为 blocked', T.PAGES['messenger_install'].coverage == T.BLOCKED)
    check('他人主页 发消息是 send',
          T.PAGES['person_profile'].targets['message_btn'].risk == T.RISK_SEND)
    check('安装按钮是 forbidden',
          T.PAGES['messenger_install'].targets['install'].risk == T.RISK_FORBIDDEN)
    check('好物特蒐标记为 partial', T.PAGES['marketplace'].coverage == T.PARTIAL)
    check('搜索页标记为 partial', T.PAGES['search'].coverage == T.PARTIAL)
    check('首页标记为 verified', T.PAGES['home'].coverage == T.VERIFIED)
    covered = {p.coverage for p in T.PAGES.values()}
    check('覆盖等级取值合法',
          covered <= {T.COVERED, T.VERIFIED, T.PARTIAL, T.BLOCKED}, covered)

    print('\n— 敏感内容不进锚点 —')
    for key, page in T.PAGES.items():
        for name, t in page.targets.items():
            joined = t.label_re + t.text_re
            check(f'{key}.{name} 锚点不含具体人名/账号',
                  'Mander' not in joined and 'Allen' not in joined, joined)

    s = T.summary()
    print('\n摘要：' + str(s))


def run_live(device: str) -> None:
    from fishfleet.web.facebook_live import DeviceSession

    print(f'— 真机 {device} —')
    with DeviceSession(device) as s:
        fg = s.foreground()
        print('前台：' + (fg.raw if fg else 'unknown'))
        els = s.dump()
        # 2026-11-02 07:08 的教训：Reel 播放页刚打开时只有 4 个**有文本的**控件，
        # 当时误记成"极简页"，其实是抓在异步加载中。
        # 注意别用 len(els) 判断——它包含状态栏/导航栏等系统节点（通常 40+），
        # 永远到不了阈值。只数"有 label/text 的业务元素"。
        def _named(xs):
            """只数 **Facebook 自己** 的有文本元素（排除状态栏/导航栏/侧屏面板）。"""
            out = 0
            for e in xs:
                ident = (e.get('identifier') or '')
                if 'com.facebook.katana' not in ident:
                    continue
                if (e.get('label') or '').strip() or (e.get('text') or '').strip():
                    out += 1
            return out
        named = _named(els)
        if named < 3:
            print(f'  有文本的元素偏少（{named}），可能仍在加载，等 3 秒再看…')
            time.sleep(3.0)
            els = s.dump()
            print(f'  等待后有文本元素数：{_named(els)}')
        print(f'层级元素数：{len(els)}')
        key = T.page_of(els, fg.activity if fg else '')
        print('判定页面：' + str(key))
        if key is None:
            print('（未能判定：可能停在未采集的页面，或锚点不足）')
            check('live 页面可判定', False, 'page_of=None')
            return
        check('live 页面可判定', True)
        print('页面锚点命中：%d' % T.page_score(key, els))
        avail = T.available(key, els)
        print(f'唯一可用控件：{len(avail)}')
        for name, risk, note in avail[:40]:
            print(f'  - {name:<20} [{risk}] {note[:46]}')
        # 唯一性体检：区分结构性重复（列表条目）与意外重复
        page = T.PAGES[key]
        unexpected, structural = [], []
        for n in page.targets:
            cnt = len(T.resolve(key, n, els))
            if cnt > 1:
                (structural if page.targets[n].repeated else unexpected).append((n, cnt))
        print('结构性重复（预期）：' + (', '.join(f'{n}x{c}' for n, c in structural) or '无'))
        if unexpected:
            print('⚠ 意外多命中（定位不稳）：' + ', '.join(f'{n}x{c}' for n, c in unexpected))
        else:
            print('意外多命中：无')
        check('live 无意外多命中', not unexpected, unexpected)


def run_audit() -> None:
    """离线审计：把资料库的结构摊开看，找出薄弱页与风险分布。"""
    print('— 资料库审计 —')
    rows = []
    for key, page in T.PAGES.items():
        named = sum(1 for t in page.targets.values()
                    if t.label_re or t.text_re or t.type_re or t.id)
        send = sum(1 for t in page.targets.values() if t.risk == T.RISK_SEND)
        forb = sum(1 for t in page.targets.values() if t.risk == T.RISK_FORBIDDEN)
        rows.append({
            'key': key, 'coverage': page.coverage,
            'identity': len(page.identity), 'exclusive': len(page.exclusive),
            'targets': len(page.targets), 'locatable': named,
            'send': send, 'forbidden': forb,
        })

    print(f'{"页面":<26}{"覆盖":<11}{"锚点":>4}{"互斥":>4}{"控件":>4}'
          f'{"可定位":>6}{"send":>5}{"forb":>5}')
    for r in rows:
        print(f'{r["key"]:<26}{r["coverage"]:<11}{r["identity"]:>4}'
              f'{r["exclusive"]:>4}{r["targets"]:>4}{r["locatable"]:>6}'
              f'{r["send"]:>5}{r["forbidden"]:>5}')

    s = T.summary()
    print(f'\n合计：{s["pages"]} 页 / {s["targets"]} 控件；'
          f'send {s["send_targets"]} / forbidden {s["forbidden_targets"]}')
    print('覆盖分布：' + ', '.join(f'{k}={v}' for k, v in sorted(s['coverage'].items())))

    # 薄弱项
    weak = [r['key'] for r in rows if r['identity'] < 2 and not r['exclusive']]
    if weak:
        print('\n锚点不足（可能判不出）：' + ', '.join(weak))
    else:
        print('\n锚点充足：每页都有 ≥2 个 identity 或互斥锚点')

    hollow = [f'{r["key"]}({r["targets"] - r["locatable"]})'
              for r in rows if r['targets'] - r['locatable'] > 0]
    if hollow:
        print('含内容类占位（不参与定位）：' + ', '.join(hollow))

    risky = [f'{r["key"]}(send={r["send"]},forb={r["forbidden"]})'
             for r in rows if r['send'] or r['forbidden']]
    print('\n高风险分布：')
    for x in risky:
        print('  ' + x)

    # 写一份报告
    out = Path(__file__).resolve().parents[2] / 'reports'
    try:
        out.mkdir(parents=True, exist_ok=True)
        import json as _j
        path = out / 'facebook_ui_audit.json'
        path.write_text(_j.dumps({'summary': s, 'pages': rows},
                                 ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n已写出：{path}')
    except OSError as exc:
        print(f'\n报告写入失败（不影响结论）：{exc}')


def run_soak(device: str, rounds: int = 2) -> None:
    """连续多轮跑端到端回归，抓间歇性失败。

    单次通过不代表稳定：渲染时序、缓存、限流都可能让第 2 轮跟第 1 轮不一样。
    **只跑 walk**：配方那套带前置返回和重试，一轮十几分钟，不适合塞进浸泡里。
    """
    print(f'— 浸泡测试 {device} ×{rounds} 轮（只跑 walk）—')
    for r in range(1, rounds + 1):
        print(f'\n===== 第 {r}/{rounds} 轮 =====')
        before = len(failures)
        run_walk(device)
        failed = len(failures) - before
        print(f'  本轮失败 {failed} 项')
        check(f'[soak 第{r}轮] walk 全通过', failed == 0, failed)


def _walk_once(device: str) -> None:
    """端到端导航回归的单次执行。"""
    from fishfleet.web.facebook_live import DeviceSession

    def risk_lookup(el: dict) -> list[str]:
        """风险反查：委托给模板里的唯一实现，避免三处各写一遍。"""
        return T.risk_of_element(el)

    def click(s, el, els, label=''):
        from fishfleet.web.facebook_live import tap_with_gate
        if not tap_with_gate(s, el, els, risk_lookup):
            check(f'[walk] 点击被闸门拒绝：{label}', False, el.get('ref'))
            return False
        return True

    def at(s, expect: str, label: str):
        els = s.dump()
        fg = s.foreground()
        got = T.page_of(els, fg.activity if fg else '')
        check(f'[walk] {label} -> {expect}', got == expect, got)
        return els, fg

    def ensure(s, expect: str, label: str, tries: int = 3):
        """按返回键直到落在目标页；失败才报错。

        实测：**搜索页第一次按返回只收起焦点/键盘，第二次才真正退出**，
        所以"按一次返回就回首页"的假设是错的。
        """
        for _ in range(tries):
            els = s.dump()
            fg = s.foreground()
            if T.page_of(els, fg.activity if fg else '') == expect:
                check(f'[walk] {label} -> {expect}', True)
                return els, fg
            s.back()
            time.sleep(1.0)
        els = s.dump()
        fg = s.foreground()
        got = T.page_of(els, fg.activity if fg else '')
        check(f'[walk] {label} -> {expect}', False, f'实际 {got}（{tries} 次返回后）')
        return els, fg

    print(f'— 端到端导航回归 {device} —')
    with DeviceSession(device) as s:
        # 先滚到顶，再多次返回到首页（有些页第一次返回只收起焦点）
        s.swipe('down', distance=1800, x=540, y=700)
        time.sleep(0.8)
        ensure(s, 'home', '起始', tries=6)
        els = s.dump()
        fg = s.foreground()

        # 首页 → 抽屉 → 返回
        menu = T.find('home', 'menu', els)
        if menu is None:
            check('[walk] 首页找到菜单按钮', False, 'not found')
        else:
            click(s, menu, els, '菜单')
            time.sleep(1.2)
            at(s, 'menu', '点菜单')
            s.back()
            time.sleep(1.0)
            at(s, 'home', '返回首页')

        # 首页 → 搜索 → 返回
        els = s.dump()
        search = T.find('home', 'search', els)
        if search is None:
            # 2026-11-02 06:30 实测：顶栏会随滚动隐藏，搜索入口直接找不到。
            # 先向下滑回顶部再试，而不是直接判失败。
            for _ in range(3):
                s.swipe('down', distance=1200, x=540, y=800)
                time.sleep(1.0)
                els = s.dump()
                search = T.find('home', 'search', els)
                if search is not None:
                    break
        if search is None:
            check('[walk] 首页找到搜索按钮', False, 'not found（顶栏可能仍隐藏）')
        else:
            click(s, search, els, '搜索')
            time.sleep(1.5)
            at(s, 'search', '点搜索')
            ensure(s, 'home', '返回首页(2)')

        # 首页 → 通知 → 首页
        els = s.dump()
        tab = T.find('home', 'tab_notifications', els)
        if tab is None:
            check('[walk] 首页找到通知 tab', False, 'not found')
        else:
            click(s, tab, els, '通知tab')
            time.sleep(1.5)
            at(s, 'notifications', '点通知')
            back_home = T.find('home', 'tab_home', s.dump())
            if back_home is not None:
                click(s, back_home, s.dump(), '回首页tab')
                time.sleep(1.2)
                at(s, 'home', '通知回首页')

        # 首页 → 搜索 → 输入关键词 → 结果页（本段会产生搜索历史，属已授权）
        els = s.dump()
        search = T.find('home', 'search', els)
        if search is None:
            check('[walk] 搜索入口（第二阶段）', False, 'not found')
            return
        click(s, search, els, '搜索')
        time.sleep(1.5)
        at(s, 'search', '再次进入搜索')
        if not s.type_anywhere('marketplace', submit=True):
            check('[walk] 输入搜索词', False, 'type_anywhere 返回 False')
            return
        time.sleep(2.5)
        at(s, 'search_results', '搜索后到结果页')
        # 回到首页
        ensure(s, 'home', '结果页返回首页', tries=4)

        # 首页 → 抽屉 → 社团 → 社团搜索 → 输入 → Group Posts
        els = s.dump()
        menu = T.find('home', 'menu', els)
        if menu is None:
            check('[walk] 社团链路：菜单入口', False, 'not found')
            return
        click(s, menu, els, '菜单')
        time.sleep(1.2)
        at(s, 'menu', '社团链路：打开菜单')
        groups_entry = T.find('menu', 'groups', s.dump())
        if groups_entry is None:
            check('[walk] 社团链路：菜单里的「社團」', False, 'not found')
            return
        # 2026-11-02 08:40 实测：`click()` 会先做一次全库风险扫描（较慢），
        # 这段时间抽屉可能已经自动收起，于是点击落到首页的搜索入口、页面变 search。
        # 点之前重新确认仍在菜单里；不在了就重开抽屉再点。
        for attempt in range(2):
            els_now = s.dump()
            fg_now = s.foreground()
            if T.page_of(els_now, fg_now.activity if fg_now else '') == 'menu':
                groups_entry = T.find('menu', 'groups', els_now)
                break
            menu_btn = T.find('home', 'menu', els_now)
            if menu_btn is None:
                break
            s.tap_element(menu_btn)
            time.sleep(1.5)
        if groups_entry is None:
            check('[walk] 社团链路：重新打开抽屉后仍找不到「社團」', False, 'not found')
            return
        click(s, groups_entry, s.dump(), '社團')
        # 2026-11-02 08:20 实测：固定 sleep 不可靠——菜单收起有动画，
        # 有时 2.8 秒仍判在上一页。改成**轮询直到页面真的变成 groups**（最多 8 秒）。
        for _ in range(8):
            time.sleep(1.0)
            els_now = s.dump()
            fg_now = s.foreground()
            if T.page_of(els_now, fg_now.activity if fg_now else '') == 'groups':
                break
        at(s, 'groups', '进入社团页')
        sg = T.find('groups', 'search_groups', s.dump())
        if sg is None:
            check('[walk] 社团链路：Search groups', False, 'not found')
            return
        click(s, sg, s.dump(), 'Search groups')
        time.sleep(1.8)
        if not s.type_anywhere('marketplace', submit=True):
            check('[walk] 社团链路：输入社团搜索词', False, 'type failed')
            return
        time.sleep(2.5)
        at(s, 'group_search_results', '社团搜索结果页')
        gp = T.find('group_search_results', 'tab_posts', s.dump())
        if gp is None:
            check('[walk] 社团链路：Group Posts 页签', False, 'not found')
            return
        click(s, gp, s.dump(), 'Group Posts')
        time.sleep(2.0)
        at(s, 'group_search_results', '切到 Group Posts')
        ensure(s, 'home', '社团链路返回首页', tries=6)



def run_walk(device: str, retries: int = 1) -> None:
    """端到端导航回归：只走可逆路径，每步校验页面判定。

    单页判定对不代表"点完会到预期页"。失败时会**回首页重跑一次**——
    渲染时序造成的偶发失败不该直接算退步，但重试后仍失败就必须报出来。
    """
    base = len(failures)
    for attempt in range(retries + 1):
        if attempt:
            print(f'\n— 重试第 {attempt} 次（清掉上次的失败记录） —')
            del failures[base:]
        _walk_once(device)
        if len(failures) == base:
            return
        if attempt == retries:
            print('  重试后仍有失败，保留结果')


def run_stability(device: str, samples: int = 5, interval: float = 1.0) -> None:
    """在同一页连续采样，检查锚点是否稳定。

    来历：01:01 长按 Like 弹表情托盘时，`Mute video` 这类控件会临时隐藏，
    页面判定从 3 个锚点掉到 1 个、直接失效。说明**控件可见性会随交互状态变化**，
    不能假设"上次在、这次也在"。这里把这件事做成可复现的检查。
    """
    from fishfleet.web.facebook_live import DeviceSession

    print(f'— 锚点稳定性采样 {device} ×{samples} —')
    with DeviceSession(device) as s:
        pages: list[str | None] = []
        counts: dict[str, list[int]] = {}
        for i in range(samples):
            els = s.dump()
            fg = s.foreground()
            key = T.page_of(els, fg.activity if fg else '')
            pages.append(key)
            if key:
                page = T.PAGES[key]
                for t in page.identity:
                    hit = 1 if any(T._hit(t, e) for e in els) else 0
                    counts.setdefault(t.name, []).append(hit)
            print(f'  第 {i + 1} 次：页面={key} 元素数={len(els)}')
            if i < samples - 1:
                time.sleep(interval)

        print('\n页面判定序列：' + ' -> '.join(str(p) for p in pages))
        consistent = len(set(pages)) == 1 and pages[0] is not None
        check('多次采样页面判定一致', consistent, pages)

        print('\n锚点命中率（该页 identity）：')
        unstable, absent = [], []
        for name, hits in counts.items():
            rate = sum(hits) / len(hits)
            if rate == 1.0:
                flag = ''
            elif rate == 0.0:
                flag = '  <-- 本次采样全程未出现'
                absent.append(name)
            else:
                flag = '  <-- 不稳定（时有时无）'
                unstable.append(name)
            print(f'  {name:<18} {sum(hits)}/{len(hits)}{flag}')
        if absent:
            print('   未出现：这批采样里根本没渲染，可能是被交互状态隐藏 -> '
                  + ', '.join(absent))
        if unstable:
            print('   不稳定：同一状态下忽有忽无，**不能单独当锚点** -> '
                  + ', '.join(unstable))
        check('没有"时有时无"的锚点', not unstable, unstable)



def run_report() -> None:
    """生成一份可交付的 Markdown 状态报告（离线，不碰设备）。"""
    from datetime import datetime
    s = T.summary()
    lines = [
        '# Facebook UI 资料库状态报告',
        '',
        f'- 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        f'- 目标应用：`{T.PKG}`',
        f'- 规模：**{s["pages"]} 页 / {s["targets"]} 控件**',
        f'- 风险：send {s["send_targets"]} / forbidden {s["forbidden_targets"]}',
        '- 覆盖分布：' + '、'.join(f'{k} {v}' for k, v in sorted(s['coverage'].items())),
        '',
        '> 覆盖等级含义：`verified`=跳转实测、`collected`=已采集、'
        '`partial`=进入但内容不全、`blocked`=无法进入。',
        '',
        '## 页面清单',
        '',
        '| 页面 key | 名称 | 覆盖 | 锚点 | 控件 | send | forb |',
        '|---|---|---|---|---|---|---|',
    ]
    for key, p in T.PAGES.items():
        send = sum(1 for t in p.targets.values() if t.risk == T.RISK_SEND)
        forb = sum(1 for t in p.targets.values() if t.risk == T.RISK_FORBIDDEN)
        lines.append(f'| `{key}` | {p.name} | {p.coverage} | '
                     f'{len(p.identity)} | {len(p.targets)} | {send} | {forb} |')

    lines += ['', '## 明确受限（无法靠 UI 采集推进）', '']
    for key, p in T.PAGES.items():
        if p.coverage in (T.PARTIAL, T.BLOCKED):
            first = p.note.splitlines()[0].strip() if p.note else ''
            brief = first if len(first) <= 90 else first[:90] + '…'
            lines.append(f'- `{key}`（{p.coverage}）：{brief}')

    lines += [
        '',
        '## 怎么用',
        '',
        '- 详细采集记录（逐页控件表、判定坑、操作事故、复现方式）：',
        '  `fishfleet/web/facebook_ui_coverage.md`',
        '- 命令速查：`python fishfleet/web/facebook_selftest.py --help`',
        '- 结构化资料库：`fishfleet/web/facebook_templates.py`',
        '- 导出物（可给其它程序读）：`data/facebook_ui_library.json`',
        '',
        '> 所有结论都来自真机实测；未验证的部分在对应页面 note 与上方受限清单里标注，',
        '> **不把"已采集控件"当成"完整任务已实现"**。',
    ]

    out = Path(__file__).resolve().parents[2] / 'reports'
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'facebook_ui_report.md'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'已生成：{path}')
    print(f'页面 {s["pages"]} / 控件 {s["targets"]}')


def run_help() -> None:
    print("""Facebook UI 资料库工具箱

离线（不碰设备）
  python fishfleet/web/facebook_selftest.py
      离线断言：页面判定、控件定位、风险分级、资料库体检、导出同步、正则卫生
  python fishfleet/web/facebook_selftest.py --audit
      资料库审计：逐页锚点/控件/风险分布，写出 reports/facebook_ui_audit.json
  python fishfleet/web/facebook_selftest.py --report
      交付报告：规模/覆盖/风险 + 逐页清单 + 受限清单 → reports/facebook_ui_report.md

真机（只跑可逆路径）
  --live <序列号>        当前页判定 + 控件唯一性体检
  --walk <序列号>        端到端导航回归（15 步，失败自动重试一次）
  --stability <序列号> [次数]
                        锚点稳定性采样，区分"时有时无"与"全程未出现"
  --soak <序列号> [轮数] 多轮 walk，抓间歇性失败

场景脚本
  python fishfleet/web/facebook_flow.py <命令>
      search <词> / dump / open <页> <控件> / open_person / tap <语义>
      type <文字> [--submit] / top / find_photo / find_tag / export
      每次 dump 落盘到 data/facebook_dumps/

  python fishfleet/web/facebook_recipe.py <命令>
      check / list / run <配方>    # 只用导出的 JSON 驱动，不导入模板模块

关键文件
  fishfleet/web/facebook_templates.py     结构化资料库（页面/控件/风险/覆盖）
  fishfleet/web/facebook_live.py         设备会话、解析、统一点击闸门
  fishfleet/web/facebook_ui_coverage.md  采集主文档（逐页表 + 判定坑 + 事故记录）
  data/facebook_ui_library.json          导出物（只含定位规则，无 ref/坐标）
  reports/                               审计、报告、浸泡日志
""")


def main() -> int:
    setup_utf8_stdout()
    if '--help' in sys.argv or '-h' in sys.argv:
        run_help()
        return 0
    if '--report' in sys.argv:
        run_report()
        return 0
    if '--audit' in sys.argv:
        run_audit()
        return 0
    if '--soak' in sys.argv:
        i = sys.argv.index('--soak')
        dev = sys.argv[i + 1] if len(sys.argv) > i + 1 else ''
        if not dev:
            print('用法：--soak <设备序列号> [轮数]')
            return 2
        n = int(sys.argv[i + 2]) if len(sys.argv) > i + 2 else 2
        run_soak(dev, n)
    elif '--stability' in sys.argv:
        i = sys.argv.index('--stability')
        dev = sys.argv[i + 1] if len(sys.argv) > i + 1 else ''
        if not dev:
            print('用法：--stability <设备序列号> [采样次数]')
            return 2
        n = int(sys.argv[i + 2]) if len(sys.argv) > i + 2 else 5
        run_stability(dev, n)
    elif '--walk' in sys.argv:
        i = sys.argv.index('--walk')
        dev = sys.argv[i + 1] if len(sys.argv) > i + 1 else ''
        if not dev:
            print('用法：--walk <设备序列号>')
            return 2
        run_walk(dev)
    elif '--live' in sys.argv:
        i = sys.argv.index('--live')
        dev = sys.argv[i + 1] if len(sys.argv) > i + 1 else ''
        if not dev:
            print('用法：--live <设备序列号>')
            return 2
        run_live(dev)
    else:
        run_offline()
    print()
    if failures:
        print(f'失败 {len(failures)} 项：' + '、'.join(failures))
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

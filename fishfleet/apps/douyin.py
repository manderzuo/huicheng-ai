"""
FishFleet — 抖音适配器

基于之前踩坑经验实现：
  · SEND intent 直达发布页（不点相册）
  · 需要三星输入法才能出现「发送」
  · 「下一步」有时渲染慢，要轮询
"""

from __future__ import annotations

import os
import time

from ..core.locator import ElementSpec
from ..flows.publish import PlatformAdapter


class DouyinAdapter(PlatformAdapter):
    name = "douyin"
    package = "com.ss.android.ugc.aweme"

    # ---- 选择器 ----

    # 发布入口（多种可能的文案）
    SEL_PUBLISH_ENTRY = [
        ElementSpec(text_contains="发布"),
        ElementSpec(text_contains="创作"),
        ElementSpec(desc_contains="发布"),
    ]

    # 编辑框
    SEL_EDIT_BOX = [
        ElementSpec(class_name="android.widget.EditText", index=0),
    ]

    # 下一步/发布按钮
    SEL_NEXT = [
        ElementSpec(text="下一步"),
        ElementSpec(text_contains="下一步"),
    ]
    SEL_SUBMIT = [
        ElementSpec(text_contains="发布"),
        ElementSpec(desc_contains="发布"),
    ]

    # 成功标志（回到首页/出现"我"）
    SEL_SUCCESS = [
        ElementSpec(text_contains="推荐"),
        ElementSpec(text_contains="首页"),
        ElementSpec(desc_contains="首页"),
    ]

    # ---- 实现 ----

    def open_publish_page(self, media: str) -> bool:
        """
        打开发布页。

        参数 media 可能是：
          · content://media/external/video/media/123   ← 已推送并识别
          · /sdcard/Download/xxx.mp4                   ← 已推送但未识别
          · C:\\Users\\...\\1.mp4                        ← 本地路径（不应该出现）

        策略：
          1. 确保视频在手机上（调用方已做，这里兜底再查一次）
          2. 用 SEND intent 直接推视频
          3. 等发布页出现
        """
        # 0. 兜底：如果传进来的是本地路径，先推送
        if media and not media.startswith("content://") and not media.startswith("/"):
            from ..core.media import MediaPusher
            push = MediaPusher(self.ctrl.serial).ensure_video(media)
            if not push.ok:
                return False
            media = push.content_uri or push.remote_path

        # 1. 先看是否已在抖音
        if self.ctrl.current_package() != self.package:
            if not self.ctrl.launch_app(self.package):
                return False
            time.sleep(3)

        # 2. 确保有可用的 content uri
        uri = ""
        if media.startswith("content://"):
            uri = media
        elif media.startswith("/"):
            # 是手机上的路径，查 MediaStore
            from ..core.media import MediaPusher
            pusher = MediaPusher(self.ctrl.serial)
            uri, _ = pusher.wait_for_media(media, timeout=10.0)

        if not uri:
            return False

        # 3. 用 SEND intent 推送视频到抖音
        try:
            self.ctrl.d.shell(
                f"am start -a android.intent.action.SEND -t video/* "
                f"--eu android.intent.extra.STREAM {uri} "
                f"-n com.ss.android.ugc.aweme/com.ss.android.ugc.aweme.adaptation.render.RenderActivity"
            )
            time.sleep(5)
        except Exception:  # noqa: BLE001
            # 兜底：不带 explicit component
            try:
                self.ctrl.d.shell(
                    f"am start -a android.intent.action.SEND -t video/* "
                    f"--eu android.intent.extra.STREAM {uri}"
                )
                time.sleep(5)
            except Exception:  # noqa: BLE001
                return False

        # 4. 关弹窗
        self.dismiss()

        # 5. 等发布页特征出现
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.try_find(self.SEL_NEXT, timeout=1.0):
                return True
            if self.try_find(self.SEL_EDIT_BOX, timeout=1.0):
                return True
            self.dismiss()
            time.sleep(1)

        return False

    def fill_metadata(self, title: str, description: str, tags: list[str]) -> bool:
        """填写标题/描述/标签。"""
        # 抖音只有一个编辑框，标题和描述合并
        text_parts = []
        if title:
            text_parts.append(title)
        if description:
            text_parts.append(description)
        if tags:
            text_parts.append(" ".join(f"#{t.lstrip('#')}" for t in tags))

        content = "\n".join(text_parts)
        if not content:
            return True

        # 1. 找编辑框
        box = self.try_find(self.SEL_EDIT_BOX, timeout=5.0)
        if box is None:
            # 尝试点「下一步」后编辑框可能出现
            nxt = self.try_find(self.SEL_NEXT, timeout=2.0)
            if nxt:
                self.ctrl.tap(nxt.x, nxt.y)
                time.sleep(2)
                box = self.try_find(self.SEL_EDIT_BOX, timeout=5.0)

        if box is None:
            return False

        # 2. 点击并输入
        self.ctrl.tap(box.x, box.y)
        time.sleep(1)
        self.ctrl.input_text(content)

        # 3. 收键盘（重要：不收键盘会挡住发布按钮）
        self.ctrl.press_key("back")
        time.sleep(1)
        return True

    def submit(self) -> bool:
        """点发布。"""
        # 可能需要先点「下一步」
        nxt = self.try_find(self.SEL_NEXT, timeout=3.0)
        if nxt:
            self.ctrl.tap(nxt.x, nxt.y)
            time.sleep(3)
            self.dismiss()

        # 找「发布」按钮
        btn = self.try_find(self.SEL_SUBMIT, timeout=6.0)
        if btn is None:
            return False

        self.ctrl.tap(btn.x, btn.y)
        time.sleep(3)

        # 处理发布后的弹窗
        self.dismiss()
        return True

    def verify_success(self) -> bool:
        """验证：是否回到首页/推荐页。"""
        for _ in range(3):
            pkg = self.ctrl.current_package()
            if pkg == self.package:
                if self.try_find(self.SEL_SUCCESS, timeout=1.0):
                    return True
            time.sleep(1.5)
        return False

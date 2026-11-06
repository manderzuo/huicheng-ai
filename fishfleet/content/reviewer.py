"""
FishFleet — 内容审核

两层审核：
  1. 规则层（快、免费）：时长/分辨率/文件大小/黑名单词
  2. LLM 层（慢、准）：内容合规/质量/平台适配

审核不通过的内容不会进入发布流程。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..llm.client import get_client


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------

@dataclass
class ReviewResult:
    passed: bool
    score: float = 0.0                     # 0-100
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    rule_check: dict = field(default_factory=dict)
    llm_check: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "rule_check": self.rule_check,
            "llm_check": self.llm_check,
        }


# ---------------------------------------------------------------------------
# 规则层
# ---------------------------------------------------------------------------

class RuleChecker:
    """快速规则检查。"""

    # 敏感词（示例，实际要按平台规则配置）
    BANNED_WORDS = [
        "涉政", "涉黄", "赌博", "毒品", "枪支",
        "加微信", "加V", "私聊", "转账",
        "最便宜", "第一", "国家级",     # 广告法极限词
    ]

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.min_duration = cfg.get("min_duration", 5)
        self.max_duration = cfg.get("max_duration", 300)
        self.min_size_mb = cfg.get("min_size_mb", 0.5)
        self.max_size_mb = cfg.get("max_size_mb", 500)
        self.banned_words = cfg.get("banned_words", self.BANNED_WORDS)

    def check_video(self, path: str) -> dict:
        """检查视频文件本身。"""
        result = {"ok": True, "problems": [], "info": {}}

        p = Path(path)
        if not p.exists():
            result["ok"] = False
            result["problems"].append("文件不存在")
            return result

        size_mb = p.stat().st_size / 1024 / 1024
        result["info"]["size_mb"] = round(size_mb, 2)

        if size_mb < self.min_size_mb:
            result["ok"] = False
            result["problems"].append(f"文件太小: {size_mb:.2f}MB")
        if size_mb > self.max_size_mb:
            result["ok"] = False
            result["problems"].append(f"文件太大: {size_mb:.1f}MB")

        # 尝试读时长（需要 ffprobe 或 imageio）
        duration = self._probe_duration(path)
        if duration is not None:
            result["info"]["duration"] = round(duration, 2)
            if duration < self.min_duration:
                result["ok"] = False
                result["problems"].append(f"时长太短: {duration:.1f}s")
            if duration > self.max_duration:
                result["ok"] = False
                result["problems"].append(f"时长太长: {duration:.1f}s")

        return result

    def check_text(self, *texts: str) -> dict:
        """检查文案。"""
        result = {"ok": True, "problems": []}
        combined = " ".join(t for t in texts if t)

        for w in self.banned_words:
            if w in combined:
                result["ok"] = False
                result["problems"].append(f"包含敏感词: {w}")

        return result

    @staticmethod
    def _probe_duration(path: str) -> Optional[float]:
        """读视频时长。优先 ffprobe，其次 imageio。"""
        # ffprobe
        try:
            import subprocess
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip())
        except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
            pass

        # imageio
        try:
            import imageio.v3 as iio
            meta = iio.immeta(path)
            if "duration" in meta:
                return float(meta["duration"])
            if "fps" in meta and "nframes" in meta:
                return float(meta["nframes"]) / float(meta["fps"])
        except Exception:  # noqa: BLE001
            pass

        return None


# ---------------------------------------------------------------------------
# LLM 层
# ---------------------------------------------------------------------------

REVIEW_PROMPT = """你是内容审核员。请审核这条短视频是否适合发布到 {platform}。

## 内容信息
- 标题：{title}
- 描述：{description}
- 标签：{tags}
- 时长：{duration} 秒

## 审核维度

1. **合规性**（一票否决）
   - 是否含违规内容（政治/色情/暴力/违法）
   - 是否含广告法极限词
   - 是否含未授权品牌/人物

2. **质量**（0-100 分）
   - 内容是否完整、有意义
   - 是否有明显 AI 生成痕迹（如畸形、闪烁）
   - 音画是否协调

3. **平台适配**（0-100 分）
   - 是否符合 {platform} 的调性
   - 标题是否有吸引力
   - 标签是否精准

## 输出格式（严格 JSON）

{{
  "passed": true/false,
  "score": 0-100,
  "reasons": ["通过/不通过的主要原因"],
  "warnings": ["需要提醒但不影响发布的问题"],
  "suggestions": ["改进建议"]
}}

只输出 JSON，不要解释。
"""


class LLMReviewer:
    def __init__(self, role: str = "vision"):
        self.client = get_client(role)

    def review(self, video_path: str, title: str, description: str,
               tags: list[str], platform: str, duration: float = 0) -> dict:
        prompt = REVIEW_PROMPT.format(
            platform=platform,
            title=title,
            description=description,
            tags=", ".join(tags),
            duration=duration,
        )

        # 如果有视觉模型，带上截图
        frames = self._extract_frames(video_path, count=3)
        if frames:
            resp = self.client.chat_with_image(prompt, frames)
        else:
            resp = self.client.chat([{"role": "user", "content": prompt}], json_mode=True)

        try:
            return json.loads(resp.content)
        except json.JSONDecodeError:
            return {
                "passed": False,
                "score": 0,
                "reasons": ["审核模型返回格式错误"],
                "raw": resp.content[:500],
            }

    @staticmethod
    def _extract_frames(video_path: str, count: int = 3) -> list[str]:
        """从视频抽帧（用于视觉审核）。"""
        out_dir = Path("data/frames")
        out_dir.mkdir(parents=True, exist_ok=True)

        frames = []
        try:
            import imageio.v3 as iio
            import numpy as np
            from PIL import Image

            # 读所有帧的索引
            props = iio.improps(video_path, plugin="pyav")
            n = props.shape[0] if hasattr(props, "shape") else 0
            if n == 0:
                return []

            indices = [int(n * (i + 1) / (count + 1)) for i in range(count)]
            for idx in indices:
                try:
                    frame = iio.imread(video_path, index=idx, plugin="pyav")
                    img = Image.fromarray(frame)
                    p = out_dir / f"{Path(video_path).stem}_{idx}.jpg"
                    img.save(p, quality=75)
                    frames.append(str(p))
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

        return frames


# ---------------------------------------------------------------------------
# 统一审核
# ---------------------------------------------------------------------------

class ContentReviewer:
    def __init__(self, config: dict | None = None):
        self.rules = RuleChecker(config)
        self._llm: Optional[LLMReviewer] = None

    @property
    def llm(self) -> LLMReviewer:
        if self._llm is None:
            self._llm = LLMReviewer()
        return self._llm

    def review(
        self,
        video_path: str,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
        platform: str = "抖音",
        use_llm: bool = True,
    ) -> ReviewResult:
        tags = tags or []
        result = ReviewResult(passed=True, score=100.0)

        # ---- 规则层 ----
        rule = self.rules.check_video(video_path)
        result.rule_check = rule
        if not rule["ok"]:
            result.passed = False
            result.reasons.extend(rule["problems"])
            result.score = 0

        text_rule = self.rules.check_text(title, description, " ".join(tags))
        result.rule_check["text"] = text_rule
        if not text_rule["ok"]:
            result.passed = False
            result.reasons.extend(text_rule["problems"])
            result.score = 0

        # 规则都没过就别浪费 LLM
        if not result.passed:
            return result

        # ---- LLM 层 ----
        if use_llm:
            duration = rule.get("info", {}).get("duration", 0)
            llm_result = self.llm.review(video_path, title, description, tags, platform, duration)
            result.llm_check = llm_result

            result.score = float(llm_result.get("score", 0))
            if not llm_result.get("passed", False):
                result.passed = False
            result.reasons.extend(llm_result.get("reasons", []))
            result.warnings.extend(llm_result.get("warnings", []))
            result.suggestions.extend(llm_result.get("suggestions", []))

        return result

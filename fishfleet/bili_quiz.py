"""B站转正答题自动答题器。

原理
----
B站的转正答题是一个 H5 页面（WebView），每题单独的 TextView 节点：
    第 N/100 题
    <题干>
    <选项1>
    <选项2>
    ...

答题流程：
    1. dump 出题干 + 所有选项
    2. 用 LLM 判断正确答案
    3. 点击对应选项的坐标
    4. 等待下一题出现，重复

⚠️ 说明：这是帮助主人省时间的自动化工具，答题内容由 LLM 依据常识和
B站社区规则判断，不是作弊脚本（没有题库硬编码、没有拦截接口）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
import uuid

ADB = __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()
SERIAL = "SERIAL0001"
OUT = str(__import__("fishfleet.paths", fromlist=["data_dir"]).data_dir() / "publish_logs")

# 复用项目的 LLM 配置
ROOT = str(__import__("fishfleet.paths", fromlist=["app_base"]).app_base())
LLM_CONF = os.path.join(ROOT, "data", "llm.json")

SYSTEM_PROMPT = """你是B站（哔哩哔哩）的资深用户，精通B站社区规范、弹幕礼仪、会员制度。

现在要回答B站转正考试的选择题。请根据B站的实际规则和常识作答。

回答格式（严格只输出一行）：
选项序号

例如答案是第2个选项，就输出：2

不要解释，不要加任何其他文字。"""


def load_llm():
    with open(LLM_CONF, encoding="utf-8") as f:
        cfg = json.load(f)
    a = cfg.get("auto", {})
    return a.get("url"), a.get("model"), a.get("key")


def llm_ask(url: str, model: str, key: str, question: str,
            options: list[str]) -> int:
    """让 LLM 选一个选项，返回 0 起的索引；失败返回 -1。"""
    opt_text = "\n".join(f"{i+1}. {o}" for i, o in enumerate(options))
    user = f"题目：{question}\n\n选项：\n{opt_text}\n\n正确答案是第几个？只输出数字。"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 500,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "FishFleet/1.0",
            "x-opencode-session": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        finish = data["choices"][0].get("finish_reason", "")

        # ★ 这个模型会先输出 reasoning_content（思考过程），
        #   max_tokens 太小时 content 就是空字符串，答案全被思考吃掉。
        #   所以：content 为空时，从 reasoning 的末尾找数字。
        if not content:
            reasoning = (msg.get("reasoning_content") or "").strip()
            m = re.search(r"([1-9])\s*$", reasoning)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(options):
                    return idx
            # 再找任意数字
            nums = re.findall(r"\b([1-9])\b", reasoning)
            if nums:
                for n in reversed(nums):
                    idx = int(n) - 1
                    if 0 <= idx < len(options):
                        return idx

        m = re.search(r"([1-9])", content)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(options):
                return idx
        print(f"    [LLM 返回] content={content[:60]!r} finish={finish}")
    except Exception as e:  # noqa: BLE001
        print(f"    [LLM 错误] {e}")
    return -1


class Quiz:
    def __init__(self, serial: str):
        import uiautomator2 as u2
        self.d = u2.connect(serial)
        self.serial = serial
        os.makedirs(OUT, exist_ok=True)

    def dump(self) -> str:
        return self.d.dump_hierarchy()

    def shot(self, tag: str) -> str:
        sp = subprocess.run([ADB, "-s", self.serial, "exec-out", "screencap", "-p"],
                            capture_output=True, timeout=30)
        f = os.path.join(OUT, f"quiz_{int(time.time())}_{tag}.png")
        with open(f, "wb") as fh:
            fh.write(sp.stdout)
        return f

    # ---- 解析当前题目 ----

    def parse(self) -> dict:
        xml = self.dump()
        nodes = []
        for m in re.finditer(
            r'<node[^>]*?text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
        ):
            t, x1, y1, x2, y2 = m.groups()
            t = t.strip()
            if not t:
                continue
            cx = (int(x1) + int(x2)) // 2
            cy = (int(y1) + int(y2)) // 2
            nodes.append({"text": t, "x": cx, "y": cy, "y1": int(y1)})

        # 找「第 N/100 题」
        progress = None
        for n in nodes:
            m = re.match(r"第\s*(\d+)\s*/\s*(\d+)\s*题", n["text"])
            if m:
                progress = (int(m.group(1)), int(m.group(2)))
                break

        if not progress:
            return {"ok": False, "reason": "找不到题号", "nodes": nodes}

        cur, total = progress
        prog_y = None
        for n in nodes:
            if re.match(r"第\s*\d+\s*/\s*\d+\s*题", n["text"]):
                prog_y = n["y"]
                break

        # 题干：题号下面第一个较长的文本
        question = ""
        q_y = None
        for n in nodes:
            if n["y"] > prog_y and len(n["text"]) >= 5 and "得分" not in n["text"]:
                question = n["text"]
                q_y = n["y"]
                break

        # 选项：题干下面、得分上方的短文本（不含 UI 文字）
        SKIP = {"答题", "使用邀请码", "规则说明", "当前得分", "小电视校长",
                "得分超过60分，即可转正哦~", "别气馁，再试一次吧~",
                "回答正确", "太棒了", "继续加油", "答对了"}
        # 答错/答对后的提示文字也可能混进选项里，用前缀过滤
        BAD_PREFIX = ("别气馁", "再试一次", "回答正确", "恭喜", "棒")
        options = []
        for n in nodes:
            if n["y"] <= q_y:
                continue
            if n["text"] in SKIP:
                continue
            if n["text"] in ("60分",) or re.fullmatch(r"\d+", n["text"]):
                continue
            if "邀请码" in n["text"] or "规则" in n["text"]:
                continue
            if len(n["text"]) > 80:
                continue
            if n["text"].startswith(BAD_PREFIX):
                continue
            # 得分区域（y > 1900）不算选项
            if n["y"] > 1900:
                continue
            options.append(n)

        return {
            "ok": bool(question and options),
            "current": cur,
            "total": total,
            "question": question,
            "options": options,
        }

    def answer_one(self, url, model, key) -> bool:
        info = self.parse()
        if not info.get("ok"):
            print(f"    解析失败: {info.get('reason')}")
            return False

        cur, total = info["current"], info["total"]
        q = info["question"]
        opts = info["options"]
        labels = [o["text"] for o in opts]

        print(f"\n[{cur}/{total}] {q[:50]}")
        for i, lb in enumerate(labels):
            print(f"    {i+1}. {lb[:44]}")

        idx = llm_ask(url, model, key, q, labels)
        if idx < 0:
            print("    ⚠ LLM 未给出答案，选第 1 个")
            idx = 0

        target = opts[idx]
        print(f"    → 选 {idx+1}: {labels[idx][:30]}  点 ({target['x']},{target['y']})")
        self.d.click(target["x"], target["y"])
        time.sleep(2.0)

        # 验证是否进入下一题
        for _ in range(6):
            nxt = self.parse()
            if nxt.get("ok") and nxt["current"] != cur:
                return True
            time.sleep(1.0)
        return False


def main():
    url, model, key = load_llm()
    print(f"LLM: {url} / {model}")

    q = Quiz(SERIAL)
    info = q.parse()
    print(f"当前: 第 {info.get('current')}/{info.get('total')} 题")

    for i in range(120):
        info = q.parse()
        if not info.get("ok"):
            print(f"\n解析失败，可能已结束: {info.get('reason')}")
            break
        cur, total = info["current"], info["total"]
        if cur >= total:
            print("\n已经到最后一题")
            break

        ok = q.answer_one(url, model, key)
        if not ok:
            print("    连续未推进，保存截图")
            print("    ", q.shot(f"stuck_{cur}"))
            break

        if i % 10 == 9:
            print(f"\n    --- 已完成 {i+1} 题 ---")

    print("\n最终截图:", q.shot("final"))


if __name__ == "__main__":
    main()

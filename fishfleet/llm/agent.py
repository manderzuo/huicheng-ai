"""
FishFleet — Agent 循环

核心思想：感知 → 决策 → 执行 → 验证

LLM 通过工具调用操作手机，每步都能看到当前状态。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .client import Message, ToolCall, get_client


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema
    handler: Callable[..., Any]
    requires_approval: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())


# 全局注册表
REGISTRY = ToolRegistry()


def tool(name: str, description: str, parameters: dict | None = None,
         requires_approval: bool = False):
    """装饰器：注册工具。"""
    def deco(fn: Callable):
        REGISTRY.register(Tool(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            handler=fn,
            requires_approval=requires_approval,
        ))
        return fn
    return deco


# ---------------------------------------------------------------------------
# Agent 状态
# ---------------------------------------------------------------------------

@dataclass
class AgentState:
    goal: str
    device: str = ""
    step: int = 0
    max_steps: int = 40
    messages: list[Message] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)   # 已执行的动作
    done: bool = False
    result: str = ""
    error: str = ""

    def add_action(self, name: str, args: dict, result: Any) -> None:
        self.history.append({
            "step": self.step,
            "tool": name,
            "args": args,
            "result": str(result)[:500],
            "ts": time.time(),
        })


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是 FishFleet 的运营机器人，负责操作安卓手机完成内容运营任务。

## 你的工作方式

1. **先看再动**：每次决策前先调用 `observe` 看清当前界面
2. **小步执行**：一次只做一个动作，做完看效果
3. **失败重试**：动作没生效就换个方法，不要死磕
4. **完成即停**：达成目标立刻调用 `finish`

## 可用工具

- `observe`：查看当前界面（截图 + UI树 + 前台应用）
- `find`：按文字/描述查找元素坐标
- `tap`：点击
- `swipe`：滑动
- `input_text`：输入文字
- `press_key`：按键（back/home/enter）
- `launch_app`：启动应用
- `wait`：等待
- `finish`：标记任务完成

## 重要规则

1. **绝不点击「拒绝」「不允许」「取消授权」** —— 会切断 USB 连接
2. **遇到弹窗先处理弹窗**，再继续原任务
3. **不确定时先截图观察**，不要瞎点
4. **发内容/发私信前必须确认**（如果工具要求审批）

## 输出格式

每次决策必须调用一个工具，用中文说明你的判断。
"""


class Agent:
    def __init__(self, device: str, llm_role: str = "default"):
        self.device = device
        self.client = get_client(llm_role)
        self.state: Optional[AgentState] = None

    # ---- 执行工具 ----

    def _execute(self, call: ToolCall) -> Any:
        t = REGISTRY.get(call.name)
        if t is None:
            return f"错误：未知工具 {call.name}"

        try:
            return t.handler(device=self.device, **call.arguments)
        except TypeError as e:
            return f"参数错误：{e}"
        except Exception as e:  # noqa: BLE001
            return f"执行失败：{type(e).__name__}: {e}"

    # ---- 主循环 ----

    def run(self, goal: str, max_steps: int = 40, verbose: bool = True) -> AgentState:
        self.state = AgentState(goal=goal, device=self.device, max_steps=max_steps)

        messages: list[Message] = [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=f"任务：{goal}\n\n设备：{self.device}\n\n请先观察当前界面。"),
        ]

        for step in range(max_steps):
            self.state.step = step

            resp = self.client.chat(messages, tools=REGISTRY.schemas())

            if resp.finish_reason == "error":
                self.state.error = str(resp.raw.get("error", "LLM 调用失败"))
                break

            # 无工具调用 → 认为结束
            if not resp.tool_calls:
                self.state.done = True
                self.state.result = resp.content
                if verbose:
                    print(f"[完成] {resp.content}")
                break

            # 记录助手消息
            messages.append(Message(
                role="assistant",
                content=resp.content,
                tool_calls=resp.tool_calls,
            ))

            # 执行所有工具调用
            for call in resp.tool_calls:
                if verbose:
                    print(f"[步骤 {step}] {call.name}({json.dumps(call.arguments, ensure_ascii=False)[:120]})")

                result = self._execute(call)
                self.state.add_action(call.name, call.arguments, result)

                messages.append(Message(
                    role="tool",
                    content=str(result)[:4000],
                    tool_call_id=call.id,
                ))

                # finish 工具 → 结束
                if call.name == "finish":
                    self.state.done = True
                    self.state.result = str(call.arguments.get("summary", ""))
                    if verbose:
                        print(f"[完成] {self.state.result}")
                    return self.state

        if not self.state.done and not self.state.error:
            self.state.error = f"超过最大步数 {max_steps}"

        return self.state


# ---------------------------------------------------------------------------
# 基础工具实现
# ---------------------------------------------------------------------------

def _ctrl(device: str):
    from ..core.actions import DeviceController
    return DeviceController(device)


@tool("observe", "查看当前界面：截图路径、UI树摘要、前台应用",
      {"type": "object", "properties": {}})
def tool_observe(device: str) -> str:
    ctrl = _ctrl(device)
    app = ctrl.current_app()
    shot = ctrl.screenshot()
    ui = ctrl.ui_dump()

    # UI 树摘要（只提取有文字的节点）
    import xml.etree.ElementTree as ET
    nodes = []
    try:
        root = ET.fromstring(ui)
        for n in root.iter("node"):
            text = (n.get("text") or "").strip()
            desc = (n.get("content-desc") or "").strip()
            if text or desc:
                nodes.append(f"{n.get('bounds')} | {text or desc}")
                if len(nodes) >= 40:
                    break
    except Exception:  # noqa: BLE001
        pass

    return json.dumps({
        "package": app.get("package", ""),
        "activity": app.get("activity", ""),
        "screenshot": shot,
        "screen": {"w": ctrl.screen.width, "h": ctrl.screen.height},
        "visible_texts": nodes,
    }, ensure_ascii=False, indent=2)


@tool("find", "按文字/描述查找元素坐标",
      {
          "type": "object",
          "properties": {
              "text": {"type": "string", "description": "要查找的文字（包含匹配）"},
              "desc": {"type": "string", "description": "content-desc（包含匹配）"},
          },
      })
def tool_find(device: str, text: str = "", desc: str = "") -> str:
    from ..core.locator import Locator, ElementSpec
    loc = Locator(device)
    spec = ElementSpec(text_contains=text or None, desc_contains=desc or None)
    spec.timeout = 3.0
    p = loc.find(spec)
    if p is None:
        return "未找到"
    return json.dumps({"x": p.x, "y": p.y, "source": p.source}, ensure_ascii=False)


@tool("tap", "点击屏幕坐标",
      {
          "type": "object",
          "properties": {
              "x": {"type": "integer"},
              "y": {"type": "integer"},
          },
          "required": ["x", "y"],
      })
def tool_tap(device: str, x: int, y: int) -> str:
    ctrl = _ctrl(device)
    ok = ctrl.tap(x, y)
    return "已点击" if ok else "点击失败"


@tool("swipe", "滑动屏幕",
      {
          "type": "object",
          "properties": {
              "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
          },
          "required": ["direction"],
      })
def tool_swipe(device: str, direction: str) -> str:
    ctrl = _ctrl(device)
    fn = {
        "up": ctrl.swipe_up,
        "down": ctrl.swipe_down,
        "left": lambda: ctrl.swipe(ctrl.screen.width - 100, ctrl.screen.height // 2, 100, ctrl.screen.height // 2),
        "right": lambda: ctrl.swipe(100, ctrl.screen.height // 2, ctrl.screen.width - 100, ctrl.screen.height // 2),
    }.get(direction)
    if fn is None:
        return f"未知方向: {direction}"
    return "已滑动" if fn() else "滑动失败"


@tool("input_text", "输入文字（支持中文）",
      {
          "type": "object",
          "properties": {"text": {"type": "string"}},
          "required": ["text"],
      })
def tool_input_text(device: str, text: str) -> str:
    ctrl = _ctrl(device)
    return "已输入" if ctrl.input_text(text) else "输入失败"


@tool("press_key", "按物理键",
      {
          "type": "object",
          "properties": {
              "key": {"type": "string", "enum": ["back", "home", "enter", "delete", "recent"]},
          },
          "required": ["key"],
      })
def tool_press_key(device: str, key: str) -> str:
    ctrl = _ctrl(device)
    return f"已按 {key}" if ctrl.press_key(key) else "按键失败"


@tool("launch_app", "启动应用",
      {
          "type": "object",
          "properties": {"package": {"type": "string"}},
          "required": ["package"],
      })
def tool_launch_app(device: str, package: str) -> str:
    ctrl = _ctrl(device)
    return f"已启动 {package}" if ctrl.launch_app(package) else "启动失败"


@tool("wait", "等待若干秒",
      {
          "type": "object",
          "properties": {"seconds": {"type": "number"}},
          "required": ["seconds"],
      })
def tool_wait(device: str, seconds: float) -> str:
    seconds = min(max(seconds, 0.5), 30)
    time.sleep(seconds)
    return f"已等待 {seconds} 秒"


@tool("finish", "任务完成",
      {
          "type": "object",
          "properties": {
              "summary": {"type": "string", "description": "完成情况说明"},
              "success": {"type": "boolean"},
          },
          "required": ["summary"],
      })
def tool_finish(device: str, summary: str, success: bool = True) -> str:
    return summary

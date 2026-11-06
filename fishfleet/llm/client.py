"""
FishFleet — LLM 客户端

OpenAI 兼容协议，支持：
  · DeepSeek / OpenAI / Moonshot / Qwen / Ollama / opencode-go
  · 工具调用（function calling）
  · 多模态（图片输入）
  · 流式（SSE）/ 非流式

注意：opencode-go 端点**只接受流式请求**，非流式会返回 400。
本客户端默认走流式，自动聚合成完整响应。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

from ..core.config import LLMConfig, get_config
from .proxy import httpx_proxy_kwargs


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    role: str                       # system / user / assistant / tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    reasoning: str = ""             # 推理内容（DeepSeek/GLM 等）

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"role": self.role}
        if self.content:
            d["content"] = self.content
        elif self.tool_calls:
            d["content"] = ""
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class LLMResponse:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


# ---------------------------------------------------------------------------
# SSE 聚合器
# ---------------------------------------------------------------------------

class _StreamAccumulator:
    """把 SSE 流聚合为完整响应。"""

    def __init__(self):
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict] = {}   # index -> {id, name, arguments}
        self.finish_reason = ""
        self.usage: dict = {}
        self.raw_chunks: list[dict] = []

    def feed(self, chunk: dict) -> None:
        self.raw_chunks.append(chunk)

        if chunk.get("usage"):
            self.usage = chunk["usage"]

        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]

            delta = choice.get("delta") or {}

            if delta.get("content"):
                self.content.append(delta["content"])
            if delta.get("reasoning_content"):
                self.reasoning.append(delta["reasoning_content"])
            if delta.get("reasoning"):
                self.reasoning.append(delta["reasoning"])

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = self.tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

    def result(self) -> LLMResponse:
        calls = []
        for idx in sorted(self.tool_calls):
            slot = self.tool_calls[idx]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=args,
            ))

        return LLMResponse(
            content="".join(self.content),
            reasoning="".join(self.reasoning),
            tool_calls=calls,
            finish_reason=self.finish_reason,
            usage=self.usage,
            raw={"chunks": len(self.raw_chunks)},
        )


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, config: LLMConfig):
        if httpx is None:
            raise RuntimeError("需要 httpx：pip install httpx")

        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.force_stream = True      # opencode-go 必须流式
        self.session_id = uuid.uuid4().hex
        self._client = httpx.Client(
            timeout=httpx.Timeout(config.timeout, connect=20.0),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "x-opencode-session": self.session_id,
            },
            **httpx_proxy_kwargs(getattr(config, "api_kind", "auto")),
        )

    # ---- 构造请求 ----

    def _payload(
        self,
        messages: list[Message] | list[dict],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        stream: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.to_dict() if isinstance(m, Message) else m for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if stream:
            # 让服务端在最后一块带 usage
            payload["stream_options"] = {"include_usage": True}
        return payload

    # ---- 流式调用 ----

    def _chat_stream(self, payload: dict) -> LLMResponse:
        acc = _StreamAccumulator()
        url = f"{self.base_url}/chat/completions"

        try:
            with self._client.stream("POST", url, json=payload) as r:
                if r.status_code >= 400:
                    body = r.read().decode("utf-8", "replace")
                    return LLMResponse(
                        finish_reason="error",
                        raw={"status": r.status_code, "body": body[:800]},
                    )

                for line in r.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            acc.feed(json.loads(data))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:  # noqa: BLE001
            return LLMResponse(finish_reason="error", raw={"error": f"{type(e).__name__}: {e}"})

        return acc.result()

    # ---- 非流式调用（兼容其他 API） ----

    def _chat_blocking(self, payload: dict) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        try:
            r = self._client.post(url, json=payload)
            if r.status_code >= 400:
                return LLMResponse(
                    finish_reason="error",
                    raw={"status": r.status_code, "body": r.text[:800]},
                )
            return self._parse(r.json())
        except Exception as e:  # noqa: BLE001
            return LLMResponse(finish_reason="error", raw={"error": f"{type(e).__name__}: {e}"})

    # ---- 统一入口 ----

    def chat(
        self,
        messages: list[Message] | list[dict],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        stream: Optional[bool] = None,
    ) -> LLMResponse:
        use_stream = self.force_stream if stream is None else stream
        payload = self._payload(messages, tools, temperature, max_tokens, json_mode, use_stream)

        if use_stream:
            resp = self._chat_stream(payload)
            # 某些服务不支持流式 → 自动降级重试一次
            if resp.finish_reason == "error":
                err = resp.raw.get("status") or resp.raw.get("error", "")
                if "400" in str(err) or "stream" in str(err).lower():
                    self.force_stream = False
                    payload["stream"] = False
                    payload.pop("stream_options", None)
                    return self._chat_blocking(payload)
            return resp

        return self._chat_blocking(payload)

    # ---- 多模态 ----

    def chat_with_image(
        self,
        prompt: str,
        image_paths: list[str],
        system: str = "",
        **kwargs,
    ) -> LLMResponse:
        import base64
        import mimetypes

        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in image_paths:
            try:
                with open(p, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                mime = mimetypes.guess_type(p)[0] or "image/png"
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            except OSError:
                continue

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        return self.chat(messages, **kwargs)

    # ---- 解析（非流式） ----

    @staticmethod
    def _parse(data: dict) -> LLMResponse:
        try:
            choice = data["choices"][0]
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            raw_calls = msg.get("tool_calls") or []

            tool_calls = []
            for tc in raw_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=args,
                ))

            return LLMResponse(
                content=content,
                reasoning=reasoning,
                tool_calls=tool_calls,
                finish_reason=choice.get("finish_reason", ""),
                usage=data.get("usage", {}),
                raw={"id": data.get("id", "")},
            )
        except (KeyError, IndexError, TypeError) as e:
            return LLMResponse(finish_reason="parse_error", raw={"error": str(e), "data": str(data)[:500]})

    # ---- 便捷方法 ----

    def ask(self, prompt: str, system: str = "") -> str:
        msgs = []
        if system:
            msgs.append(Message(role="system", content=system))
        msgs.append(Message(role="user", content=prompt))
        return self.chat(msgs).content

    def ask_json(self, prompt: str, system: str = "") -> dict:
        """要求返回 JSON（用提示词约束，不依赖 response_format）。"""
        sys_msg = (system + "\n\n" if system else "") + \
            "只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。"
        msgs = [
            Message(role="system", content=sys_msg),
            Message(role="user", content=prompt),
        ]
        resp = self.chat(msgs)
        return self._extract_json(resp.content)

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从文本里抠出 JSON（容错）。"""
        text = (text or "").strip()

        # 去 markdown 包裹
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
            text = text.strip()

        # 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 找第一个完整的 {...}
        start = text.find("{")
        if start >= 0:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break

        return {"error": "json_parse_failed", "raw": text[:500]}

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_clients: dict[str, LLMClient] = {}


def get_client(role: str = "default") -> LLMClient:
    if role not in _clients:
        cfg = get_config()
        profile = cfg.llm(role)
        # 桌面API设置与核心YAML原来互不相通：默认配置未填key时，
        # 仅默认角色使用用户已保存的自动化API；不覆盖显式核心配置或视觉角色。
        if role == 'default' and not profile.api_key:
            from pathlib import Path
            from dataclasses import replace
            path = Path(__file__).resolve().parents[2] / 'data' / 'llm.json'
            try:
                saved = json.loads(path.read_text(encoding='utf-8')).get('auto', {})
            except (OSError, ValueError):
                saved = {}
            if all(saved.get(k) for k in ('url', 'key', 'model')):
                profile = replace(profile, base_url=saved['url'], api_key=saved['key'], model=saved['model'])
        _clients[role] = LLMClient(profile)
    return _clients[role]


def reset_clients() -> None:
    for c in _clients.values():
        c.close()
    _clients.clear()

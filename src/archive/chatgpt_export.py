"""
src/archive/chatgpt_export.py
ChatGPT Data Export(conversations.json) 파서

목표:
- ZIP 또는 JSON에서 conversations.json을 읽어
- 대화(Conversation) 단위로 메시지를 추출하고
- 마크다운 변환을 위한 중립 구조를 제공합니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    role: str
    create_time: float | None
    content: str


@dataclass(frozen=True)
class Conversation:
    conv_id: str
    title: str
    create_time: float | None
    update_time: float | None
    messages: list[ChatMessage]


def _as_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _extract_text_from_content(content: Any) -> str:
    """
    ChatGPT export content 필드에서 텍스트를 최대한 보수적으로 추출합니다.
    - 일반적으로: {"content_type":"text","parts":[...]}
    """
    if not content:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            out = []
            for p in parts:
                if p is None:
                    continue
                if isinstance(p, str):
                    out.append(p)
                else:
                    out.append(str(p))
            return "\n".join(out).strip()

        # 일부 변형(예: {"text": "..."} 등)
        for k in ("text", "value", "content"):
            v = content.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    return ""


def _extract_messages_from_mapping(mapping: Any) -> list[ChatMessage]:
    if not isinstance(mapping, dict):
        return []

    msgs: list[ChatMessage] = []
    for _, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue

        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = (author.get("role") or message.get("role") or "").strip() or "unknown"
        create_time = _as_float(message.get("create_time"))

        content = _extract_text_from_content(message.get("content"))
        if not content:
            continue

        msgs.append(ChatMessage(role=role, create_time=create_time, content=content))

    # create_time 기준 정렬(없으면 뒤로)
    msgs.sort(key=lambda m: (m.create_time is None, m.create_time or 0.0))
    return msgs


def _extract_messages_from_list(messages: Any) -> list[ChatMessage]:
    if not isinstance(messages, list):
        return []

    out: list[ChatMessage] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("author") or "unknown").strip() or "unknown"
        create_time = _as_float(item.get("create_time") or item.get("timestamp"))
        content = _extract_text_from_content(item.get("content") or item.get("text"))
        if not content:
            continue
        out.append(ChatMessage(role=role, create_time=create_time, content=content))

    out.sort(key=lambda m: (m.create_time is None, m.create_time or 0.0))
    return out


def parse_conversations(conversations_json: Path) -> list[Conversation]:
    raw = json.loads(conversations_json.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []

    out: list[Conversation] = []
    for conv in raw:
        if not isinstance(conv, dict):
            continue

        conv_id = str(conv.get("id") or conv.get("conversation_id") or "").strip()
        title = str(conv.get("title") or "Untitled").strip()
        create_time = _as_float(conv.get("create_time"))
        update_time = _as_float(conv.get("update_time"))

        msgs: list[ChatMessage] = []
        if "mapping" in conv:
            msgs = _extract_messages_from_mapping(conv.get("mapping"))
        elif "messages" in conv:
            msgs = _extract_messages_from_list(conv.get("messages"))

        out.append(
            Conversation(
                conv_id=conv_id,
                title=title,
                create_time=create_time,
                update_time=update_time,
                messages=msgs,
            )
        )

    return out


def to_iso(ts: float | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(sep=" ", timespec="seconds")
    except Exception:
        return ""


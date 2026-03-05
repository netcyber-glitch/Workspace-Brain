"""
src/indexer/chunking.py
Workspace Brain — 텍스트 청킹(Chunking) 유틸

목표:
- 외부 라이브러리 없이(=표준 라이브러리만) 결정적(deterministic)으로 청크를 생성
- 청크는 (chunk_index, char_start, char_end, text, token_count)를 제공
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    token_count: int


def estimate_token_count(text: str) -> int:
    """
    토큰 수를 대략 추정합니다(정확한 tokenizer는 Phase 3+에서 고려).
    - 경험적으로 영문/코드 기준 1토큰 ≈ 4 chars 수준이라 가정
    """
    t = text or ""
    # 너무 작은 값(0) 방지
    return max(1, int(len(t) / 4))


def _trim_with_offsets(text: str, start: int, end: int) -> tuple[int, int, str]:
    raw = text[start:end]
    if not raw:
        return start, start, ""

    l = len(raw) - len(raw.lstrip())
    r = len(raw.rstrip())
    trimmed = raw[l:r]
    new_start = start + l
    new_end = start + r
    return new_start, new_end, trimmed


def _find_break_pos(text: str, start: int, end: int, soft_min: int, *, prefer_code: bool) -> int:
    """
    start~end 범위 내에서 "끊기 좋은 지점"을 찾습니다.
    - prefer_code=True면 줄바꿈 위주로 끊습니다.
    """
    if end <= start:
        return start

    s_min = min(end, start + max(0, soft_min))

    # 1) 공백 줄(문단) 경계
    if not prefer_code:
        p = text.rfind("\n\n", s_min, end)
        if p != -1:
            return p + 2

    # 2) 줄바꿈
    p = text.rfind("\n", s_min, end)
    if p != -1:
        return p + 1

    # 3) 공백
    if not prefer_code:
        p = text.rfind(" ", s_min, end)
        if p != -1:
            return p + 1

    # 4) 못 찾으면 하드 컷
    return end


def chunk_text(
    text: str,
    *,
    max_chars: int = 1400,
    overlap: int = 200,
    min_chars: int = 300,
    prefer_code: bool = False,
) -> list[TextChunk]:
    """
    슬라이딩 윈도우 기반 청킹.

    - max_chars: 청크 최대 길이(문자)
    - overlap: 다음 청크가 이전 청크와 겹치는 길이(문자)
    - min_chars: 너무 촘촘한 분할을 피하기 위한 최소 길이(끊는 지점 탐색 하한)
    """
    if not text:
        return []

    t = text.replace("\r\n", "\n")
    n = len(t)
    if n <= 0:
        return []

    max_chars = max(200, int(max_chars))
    overlap = max(0, int(overlap))
    min_chars = max(50, int(min_chars))

    chunks: list[TextChunk] = []
    start = 0
    idx = 0

    while start < n:
        hard_end = min(n, start + max_chars)
        break_pos = _find_break_pos(
            t,
            start,
            hard_end,
            soft_min=min_chars,
            prefer_code=prefer_code,
        )
        if break_pos <= start:
            break_pos = hard_end

        c_start, c_end, c_text = _trim_with_offsets(t, start, break_pos)
        if c_text:
            chunks.append(
                TextChunk(
                    chunk_index=idx,
                    char_start=c_start,
                    char_end=c_end,
                    text=c_text,
                    token_count=estimate_token_count(c_text),
                )
            )
            idx += 1

        if break_pos >= n:
            break

        next_start = max(0, break_pos - overlap)
        # 무한 루프 방지: 최소한 1 char은 전진
        if next_start <= start:
            next_start = break_pos
        start = next_start

    return chunks


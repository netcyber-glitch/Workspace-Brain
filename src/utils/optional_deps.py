"""
src/utils/optional_deps.py
Workspace Brain — 선택(옵션) 의존성 존재 여부 체크

주의:
- 실제 import를 하지 않고(find_spec) 설치 여부만 확인합니다.
  (UI 시작 시 불필요한 무거운 import를 피하기 위함)
"""

from __future__ import annotations

import importlib.util


def has_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(str(module_name)) is not None
    except Exception:
        return False


def has_vector_deps() -> bool:
    """
    Vector/Hybrid 검색 및 벡터 인덱싱에 필요한 의존성.
    """
    return has_module("chromadb") and has_module("sentence_transformers")


def has_content_sim_deps() -> bool:
    """
    버전 체인 내용 유사도(content similarity)에 필요한 의존성.
    """
    return has_module("sentence_transformers")

